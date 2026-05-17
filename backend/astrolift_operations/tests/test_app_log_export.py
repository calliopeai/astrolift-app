"""Tests for the app-log export pipeline (#483).

Mirrors :mod:`test_audit_export` for the per-app runtime log surface:
pure serialization, token round-trip, mutation gating + happy path,
filter pushdown, line cap, and the token-gated download view.

The cluster log backend is stubbed via
:func:`core.cluster_observability.set_log_backend_for_tests` so the
tests exercise the full driver dispatch path without a real k8s
cluster.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
from types import SimpleNamespace

import pytest
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.app_log_export import (
    apply_filters,
    hash_token,
    mint_token,
    serialize_csv,
    serialize_ndjson,
    serialize_txt,
    write_artifact,
)
from astrolift_operations.models import AppLogExport
from astrolift_operations.schema.mutations import (
    ExportAppLogsInput,
    OperationsMutation,
)
from astrolift_registry.models import RegisteredApp
from core.cluster_observability import (
    reset_log_backend_for_tests,
    set_log_backend_for_tests,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- helpers + fixtures ------------------------------------------------


class _LogLine:
    """Duck-typed stand-in for ``_sdk.cluster.PodLogLine``. Kept local
    so the tests don't depend on the provider SDK import path."""

    def __init__(
        self,
        *,
        pod_name: str = "hello-app-web-1",
        container: str = "web",
        timestamp: dt.datetime | None = None,
        message: str = "hello",
        stream: str = "stdout",
    ) -> None:
        self.pod_name = pod_name
        self.container = container
        self.timestamp = timestamp or dt.datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
        self.message = message
        self.stream = stream


class _FakeLogBackend:
    """Test backend that yields a fixed list of lines on every
    ``stream`` call. Mirrors the contract in
    :mod:`core.cluster_observability` so the driver dispatch path
    runs end-to-end."""

    def __init__(self, lines):
        self._lines = list(lines)

    def stream(self, *, auth, namespace, pod_name, container, tail_lines, follow):
        async def _gen():
            for line in self._lines[:tail_lines]:
                yield line

        return _gen()


@pytest.fixture
def _install_log_backend():
    installed = []

    def _install(lines):
        backend = _FakeLogBackend(lines)
        set_log_backend_for_tests(backend)
        installed.append(backend)
        return backend

    yield _install
    reset_log_backend_for_tests()


def _scaffold(
    *, slug_suffix: str = "log"
) -> tuple[Organization, RegisteredApp, AppEnvironment, TenantCluster]:
    org = Organization.objects.create(name="LogOrg", slug=f"log-org-{slug_suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug_suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{slug_suffix}")
    plugin = ProviderPlugin(
        name="K8s",
        slug=f"k8s-{slug_suffix}",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug=f"k8s-{slug_suffix}")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="dev",
        slug=f"dev-{slug_suffix}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug=f"hello-{slug_suffix}",
        k8s_namespace=f"acme-hello-{slug_suffix}",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello.example.com",
    )
    return org, app, env, cluster


def _info(request=None):
    rf = RequestFactory()
    return SimpleNamespace(
        context=SimpleNamespace(
            user=None,
            request=request or rf.get("/app/gql/config/"),
        )
    )


# ---- pure serialization ------------------------------------------------


def test_serialize_csv_has_header_and_row():
    lines = [_LogLine(message="hello world")]
    payload, n = serialize_csv(lines)
    reader = list(csv.reader(io.StringIO(payload.decode("utf-8"))))
    assert reader[0] == ["timestamp", "pod_name", "container", "stream", "message"]
    assert reader[1][4] == "hello world"
    assert n == 1


def test_serialize_ndjson_one_object_per_line():
    lines = [_LogLine(message="line-a"), _LogLine(message="line-b")]
    payload, n = serialize_ndjson(lines)
    rows = [json.loads(s) for s in payload.splitlines()]
    assert n == 2
    assert [r["message"] for r in rows] == ["line-a", "line-b"]


def test_serialize_txt_prefixes_pod_and_container():
    line = _LogLine(message="boom", pod_name="api-1", container="api")
    payload, n = serialize_txt([line])
    assert n == 1
    text = payload.decode("utf-8")
    assert "[api-1/api]" in text
    assert text.endswith("boom\n")


# ---- token helpers -----------------------------------------------------


def test_mint_token_hashes_match():
    plaintext, digest = mint_token()
    assert digest == hash_token(plaintext)
    assert len(plaintext) >= 32


# ---- filtering ---------------------------------------------------------


def test_apply_filters_level_narrows():
    lines = [
        _LogLine(message="INFO start"),
        _LogLine(message="ERROR boom"),
        _LogLine(message="info trailing"),
    ]
    out = list(apply_filters(lines, level="ERROR", regex=None, max_lines=10))
    assert [line.message for line in out] == ["ERROR boom"]


def test_apply_filters_regex_narrows():
    lines = [
        _LogLine(message="GET /foo 200"),
        _LogLine(message="GET /bar 500"),
        _LogLine(message="POST /foo 201"),
    ]
    out = list(apply_filters(lines, level=None, regex=r"\b500\b", max_lines=10))
    assert [line.message for line in out] == ["GET /bar 500"]


def test_apply_filters_caps_at_max_lines():
    lines = [_LogLine(message=f"l{i}") for i in range(20)]
    out = list(apply_filters(lines, level=None, regex=None, max_lines=3))
    assert len(out) == 3


# ---- artifact write ----------------------------------------------------


def test_write_artifact_ndjson_writes_file(tmp_path):
    lines = [_LogLine(message=f"l{i}") for i in range(3)]
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        artifact = write_artifact(lines, format="ndjson", guid="abc-guid", max_lines=100)
    assert artifact.format == "ndjson"
    assert artifact.row_count == 3
    assert artifact.byte_count > 0
    assert artifact.truncated is False
    assert os.path.exists(artifact.absolute_path)
    assert artifact.relative_path.endswith("abc-guid.ndjson")


def test_write_artifact_flags_truncation(tmp_path):
    lines = [_LogLine(message=f"l{i}") for i in range(10)]
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        artifact = write_artifact(lines, format="csv", guid="trunc-guid", max_lines=3)
    assert artifact.row_count == 3
    assert artifact.truncated is True


def test_write_artifact_rejects_unknown_format(tmp_path):
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with pytest.raises(ValueError):
            write_artifact([_LogLine()], format="parquet", guid="x", max_lines=10)


# ---- mutation end-to-end ----------------------------------------------


def test_export_denied_without_permission(tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="deny")
    _install_log_backend([_LogLine(message="hello")])
    # Only grant read-logs (the read surface), not the export gate.
    permission_resolver.grant(Permission.APP_READ_LOGS)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=1)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="TXT",
                ),
            )
    assert result.ok is False
    assert result.errors
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_export_succeeds_with_permission(tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="ok")
    _install_log_backend([_LogLine(message=f"line-{i}") for i in range(5)])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=None)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="NDJSON",
                ),
            )
    assert result.ok is True, result.errors
    payload = result.data
    assert payload is not None
    assert payload.format == "ndjson"
    assert payload.status == "ready"
    assert payload.row_count == 5
    assert payload.sha256
    assert "/app_log_exports/" in payload.download_url

    export = AppLogExport.objects.get(guid=payload.id)
    assert export.row_count == 5
    assert export.organization_id == org.id
    assert export.registered_app_id == app.id
    assert export.pod_name == "hello-app-web-1"


def test_export_rejects_bad_format(tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="fmt")
    _install_log_backend([_LogLine(message="hi")])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="xls",
                ),
            )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "format"


def test_export_rejects_unknown_app(tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="noapp")
    _install_log_backend([_LogLine(message="hi")])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug="missing-app",
                    pod_name="hello-app-web-1",
                    format="TXT",
                ),
            )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_export_rejects_unknown_environment(tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="noenv")
    _install_log_backend([_LogLine(message="hi")])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="TXT",
                    environment_name="staging-missing",
                ),
            )
    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "environmentName"


def test_export_rejects_bad_regex(tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="regex")
    _install_log_backend([_LogLine(message="hi")])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="TXT",
                    regex="[unbalanced",
                ),
            )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "regex"


def test_export_respects_level_filter(tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="lvl")
    _install_log_backend(
        [
            _LogLine(message="INFO booting"),
            _LogLine(message="ERROR oh no"),
            _LogLine(message="INFO ready"),
        ]
    )
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="NDJSON",
                    level="ERROR",
                ),
            )
    assert result.ok is True, result.errors
    assert result.data.row_count == 1


def test_export_enforces_max_lines_cap(tmp_path, permission_resolver, _install_log_backend, settings):
    org, app, env, cluster = _scaffold(slug_suffix="cap")
    _install_log_backend([_LogLine(message=f"l{i}") for i in range(20)])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    # Pin a tiny cap via constance override. The mutation reads the
    # cap from ``constance.config`` at call time, so monkey-patching
    # the module attribute is the cleanest way to drive it under test.
    from constance import config as constance_config

    original = getattr(constance_config, "APP_LOG_EXPORT_MAX_LINES", 100000)
    constance_config.APP_LOG_EXPORT_MAX_LINES = 5
    try:
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            with tenant_context(TenantContext(organization_id=org.id)):
                m = OperationsMutation()
                result = m.export_astrolift_app_logs(
                    _info(),
                    ExportAppLogsInput(
                        app_slug=app.slug,
                        pod_name="hello-app-web-1",
                        format="NDJSON",
                    ),
                )
    finally:
        constance_config.APP_LOG_EXPORT_MAX_LINES = original

    assert result.ok is True, result.errors
    assert result.data.row_count == 5
    # Truncated metadata snapshots into filters_snapshot.
    export = AppLogExport.objects.get(guid=result.data.id)
    assert export.filters_snapshot.get("truncated") is True


# ---- download view -----------------------------------------------------


def test_download_returns_file_and_stamps_consumed(
    client, tmp_path, permission_resolver, _install_log_backend
):
    org, app, env, cluster = _scaffold(slug_suffix="dl")
    _install_log_backend([_LogLine(message="hello-bytes")])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="TXT",
                ),
            )
        assert result.ok is True, result.errors
        # The download URL is absolute; pull the path off the end and
        # exercise it via the test client (which uses MEDIA_ROOT from
        # override_settings above).
        relative = re.search(r"/app/app_log_exports/[^?]+/", result.data.download_url)
        assert relative is not None
        response = client.get(relative.group(0))
        assert response.status_code == 200
        assert b"hello-bytes" in b"".join(response.streaming_content)

    export = AppLogExport.objects.get(guid=result.data.id)
    assert export.consumed_at is not None


def test_download_rejects_bad_token(client, tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="badtok")
    _install_log_backend([_LogLine(message="hi")])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="TXT",
                ),
            )
        assert result.ok is True, result.errors
        url = reverse(
            "astrolift_operations:app-log-export-download",
            kwargs={"guid": str(result.data.id), "token": "totally-wrong-token"},
        )
        response = client.get(url)
        assert response.status_code == 404


def test_download_rejects_expired(client, tmp_path, permission_resolver, _install_log_backend):
    org, app, env, cluster = _scaffold(slug_suffix="exp")
    _install_log_backend([_LogLine(message="hi")])
    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        with tenant_context(TenantContext(organization_id=org.id)):
            m = OperationsMutation()
            result = m.export_astrolift_app_logs(
                _info(),
                ExportAppLogsInput(
                    app_slug=app.slug,
                    pod_name="hello-app-web-1",
                    format="TXT",
                ),
            )
        assert result.ok is True, result.errors
        AppLogExport.objects.filter(guid=result.data.id).update(
            expires_at=timezone.now() - dt.timedelta(seconds=10),
        )
        relative = re.search(r"/app/app_log_exports/[^?]+/", result.data.download_url)
        response = client.get(relative.group(0))
        assert response.status_code == 404
