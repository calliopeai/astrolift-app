"""Tests for the token-gated audit export download view (#433)."""

from __future__ import annotations

import datetime as dt
import os

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.audit_export import hash_token
from astrolift_operations.models import AuditExport

pytestmark = pytest.mark.django_db


def _mkorg() -> Organization:
    return Organization.objects.create(name="DownloadOrg", slug="download-org")


def _write_artifact(tmp_path, name: str, body: bytes) -> str:
    subdir = tmp_path / "audit_exports"
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / name).write_bytes(body)
    return os.path.join("audit_exports", name)


def _mkexport(org, tmp_path, *, token: str, ttl_seconds: int = 600) -> AuditExport:
    body = b"occurred_at,guid\n2026-01-01T00:00:00Z,abc\n"
    relative = _write_artifact(tmp_path, "ex-1.csv", body)
    return AuditExport.objects.create(
        organization=org,
        format="csv",
        row_count=1,
        byte_count=len(body),
        sha256="deadbeef",
        relative_path=relative,
        token_hash=hash_token(token),
        expires_at=timezone.now() + dt.timedelta(seconds=ttl_seconds),
    )


def test_download_serves_file_when_token_matches(tmp_path):
    org = _mkorg()
    token = "abc123"
    export = _mkexport(org, tmp_path, token=token)
    client = Client()
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        resp = client.get(f"/app/audit_exports/{export.guid}/{token}/")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/csv")
    assert resp["X-Audit-Export-Sha256"] == "deadbeef"
    assert b"occurred_at,guid" in b"".join(resp.streaming_content)


def test_download_404_when_token_mismatches(tmp_path):
    org = _mkorg()
    export = _mkexport(org, tmp_path, token="correct")
    client = Client()
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        resp = client.get(f"/app/audit_exports/{export.guid}/wrong/")
    assert resp.status_code == 404


def test_download_404_when_expired(tmp_path):
    org = _mkorg()
    export = _mkexport(org, tmp_path, token="x")
    AuditExport.objects.filter(pk=export.pk).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    client = Client()
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        resp = client.get(f"/app/audit_exports/{export.guid}/x/")
    assert resp.status_code == 404


def test_download_404_when_file_missing(tmp_path):
    org = _mkorg()
    export = _mkexport(org, tmp_path, token="x")
    # Remove the artifact from disk after creation
    full = tmp_path / export.relative_path
    full.unlink()
    client = Client()
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        resp = client.get(f"/app/audit_exports/{export.guid}/x/")
    assert resp.status_code == 404


def test_download_stamps_consumed_at_on_first_get(tmp_path):
    org = _mkorg()
    export = _mkexport(org, tmp_path, token="x")
    assert export.consumed_at is None
    client = Client()
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        resp = client.get(f"/app/audit_exports/{export.guid}/x/")
    assert resp.status_code == 200
    refreshed = AuditExport.objects.get(pk=export.pk)
    assert refreshed.consumed_at is not None
