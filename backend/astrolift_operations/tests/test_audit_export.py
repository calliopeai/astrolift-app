"""Tests for the audit-log export pipeline (#433 scope C)."""

from __future__ import annotations

import csv
import io
import json
import os
from types import SimpleNamespace

import pytest
from django.test import RequestFactory, override_settings

from astrolift_identity.models import Organization
from astrolift_operations.audit_export import (
    hash_token,
    mint_token,
    serialize_csv,
    serialize_ndjson,
    write_artifact,
)
from astrolift_operations.audit_redaction import REDACTED_SENTINEL
from astrolift_operations.models import AuditEvent, AuditExport
from astrolift_operations.schema.mutations import (
    ExportAuditEventsInput,
    OperationsMutation,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(request=None):
    rf = RequestFactory()
    return SimpleNamespace(
        context=SimpleNamespace(
            user=None,
            request=request or rf.get("/app/gql/config/"),
        )
    )


def _mkorg() -> Organization:
    return Organization.objects.create(name="ExportOrg", slug="export-org")


def _mkaudit(org: Organization, action: str = "team.create", **data) -> AuditEvent:
    return AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id="42",
        actor_display="alice",
        action=action,
        decision=AuditEvent.Decision.ALLOW,
        target_kind="Team",
        target_id="t-1",
        target_slug="alpha",
        data=data or {"reason": "ok"},
    )


# ---- pure serialization ------------------------------------------------


def test_serialize_csv_has_header_and_row(db):
    org = _mkorg()
    row = _mkaudit(org)
    payload, n = serialize_csv([row])
    text = payload.decode("utf-8")
    reader = list(csv.reader(io.StringIO(text)))
    assert reader[0][0] == "occurred_at"
    assert reader[1][6] == "team.create"  # action column
    assert n == 1


def test_serialize_ndjson_one_object_per_line(db):
    org = _mkorg()
    rows = [_mkaudit(org), _mkaudit(org, action="team.delete")]
    payload, n = serialize_ndjson(rows)
    lines = payload.splitlines()
    assert n == 2
    assert len(lines) == 2
    decoded = [json.loads(line) for line in lines]
    actions = sorted(d["action"] for d in decoded)
    assert actions == ["team.create", "team.delete"]


def test_serialize_csv_redacts_secret(db):
    org = _mkorg()
    row = _mkaudit(org, secret="ssss", api_key="k")
    payload, _ = serialize_csv([row])
    text = payload.decode("utf-8")
    assert "ssss" not in text
    assert 'k"' not in text or REDACTED_SENTINEL in text
    assert REDACTED_SENTINEL in text


def test_serialize_ndjson_redacts_nested_secret(db):
    org = _mkorg()
    row = _mkaudit(org, before={"app": {"secret_key": "x"}}, after={"app": {"secret_key": "y"}})
    payload, _ = serialize_ndjson([row])
    text = payload.decode("utf-8")
    assert '"x"' not in text
    assert '"y"' not in text
    parsed = json.loads(text.splitlines()[0])
    assert parsed["data"]["before"]["app"]["secret_key"] == REDACTED_SENTINEL
    assert parsed["data"]["after"]["app"]["secret_key"] == REDACTED_SENTINEL


# ---- token helpers -----------------------------------------------------


def test_mint_token_hashes_match():
    plaintext, digest = mint_token()
    assert digest == hash_token(plaintext)
    assert len(plaintext) >= 32


# ---- artifact write ----------------------------------------------------


def test_write_artifact_csv_writes_file(db, tmp_path):
    org = _mkorg()
    rows = [_mkaudit(org)]
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        artifact = write_artifact(rows, format="csv", guid="abc-guid")
    assert artifact.format == "csv"
    assert artifact.row_count == 1
    assert artifact.byte_count > 0
    assert os.path.exists(artifact.absolute_path)
    assert artifact.relative_path.endswith("abc-guid.csv")


def test_write_artifact_rejects_unknown_format(db, tmp_path):
    org = _mkorg()
    rows = [_mkaudit(org)]
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with pytest.raises(ValueError):
            write_artifact(rows, format="parquet", guid="abc")


# ---- mutation end-to-end ----------------------------------------------


def test_export_denied_without_permission(db, tmp_path, permission_resolver):
    org = _mkorg()
    _mkaudit(org)
    permission_resolver.grant(Permission.AUDIT_LOG_READ)  # read only
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=1)):
            m = OperationsMutation()
            result = m.export_audit_events(
                _info(),
                ExportAuditEventsInput(format="CSV"),
            )
    assert result.ok is False
    assert result.errors
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_export_succeeds_with_export_permission(db, tmp_path, permission_resolver):
    org = _mkorg()
    for i in range(3):
        _mkaudit(org, action=f"team.action{i}")
    permission_resolver.grant(Permission.AUDIT_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=None)):
            m = OperationsMutation()
            result = m.export_audit_events(
                _info(),
                ExportAuditEventsInput(format="ndjson"),
            )
    assert result.ok is True, result.errors
    payload = result.data
    assert payload is not None
    assert payload.format == "ndjson"
    assert payload.row_count == 3
    assert payload.download_url.endswith("/")
    assert "/audit_exports/" in payload.download_url
    assert payload.sha256

    export = AuditExport.objects.get(guid=payload.id)
    assert export.row_count == 3
    assert export.organization_id == org.id


def test_export_respects_action_filter(db, tmp_path, permission_resolver):
    org = _mkorg()
    _mkaudit(org, action="team.create")
    _mkaudit(org, action="team.delete")
    permission_resolver.grant(Permission.AUDIT_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_audit_events(
                _info(),
                ExportAuditEventsInput(format="CSV", action="team.delete"),
            )
    assert result.ok is True
    assert result.data.row_count == 1


def test_export_rejects_bad_format(db, tmp_path, permission_resolver):
    org = _mkorg()
    permission_resolver.grant(Permission.AUDIT_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_audit_events(
                _info(),
                ExportAuditEventsInput(format="xls"),
            )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "format"
