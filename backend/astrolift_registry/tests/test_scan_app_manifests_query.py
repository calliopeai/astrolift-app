"""Tests for the ``scanAppManifests`` discovery-preview query (#979).

``scan_app_manifests`` (on ``RegistryQuery``) backs the monorepo /
multi-service discovery step of the app onboarding wizard: it scans a repo for
app manifests and returns a preview WITHOUT persisting anything. These tests
pin, against a real database:

  * the preview lists each app manifest found (path + name + build_context);
  * ``already_registered`` reflects whether an app for that repo + manifest
    path already exists;
  * the query is tenant-scoped — it reads the active tenant's organization and
    fetches through that org's source connection (no foreign-org read);
  * the read gate is ``app.read``;
  * nothing is persisted by the preview.

The SCM tree fetch is patched at the service's ``_default_tree_fetch`` so the
query runs end-to-end with no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _app_toml(name: str) -> str:
    return (
        f'name = "{name}"\n'
        "[[workloads]]\n"
        f'name = "{name}"\n'
        'kind = "deployment"\n'
        "is_public = true\n"
        "[[workloads.containers]]\n"
        f'name = "{name}"\n'
        "is_primary = true\n"
        "port = 8080\n"
    )


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme")


def _connect(org):
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_PAT,
        display_name="PAT",
        account_login=org.slug,
    )


def _patch_tree(monkeypatch, files):
    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(ms, "_default_tree_fetch", lambda connection, repo, ref: dict(files))


def test_scan_returns_preview_rows(monkeypatch, info, org, permission_resolver):
    _connect(org)
    permission_resolver.grant(Permission.APP_READ)
    _patch_tree(
        monkeypatch,
        {
            "apps/web/astrolift.toml": _app_toml("web"),
            "apps/api/astrolift.toml": _app_toml("api"),
        },
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().scan_app_manifests(info, source_repo="acme/monorepo")

    assert result.ok is True
    assert result.error is None
    by_name = {a.name: a for a in result.apps}
    assert set(by_name) == {"web", "api"}
    assert by_name["web"].manifest_path == "apps/web/astrolift.toml"
    assert by_name["web"].build_context == "apps/web"
    assert by_name["web"].workload_count == 1
    assert all(a.already_registered is False for a in result.apps)
    # Preview persists nothing.
    assert RegisteredApp.objects.filter(organization=org).count() == 0


def test_scan_flags_already_registered(monkeypatch, info, org, permission_resolver):
    _connect(org)
    permission_resolver.grant(Permission.APP_READ)
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    # Pre-register one app at the same repo + manifest path.
    RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Web",
        slug="web",
        source_kind="github",
        source_repo="acme/monorepo",
        manifest_path="apps/web/astrolift.toml",
    )
    _patch_tree(
        monkeypatch,
        {
            "apps/web/astrolift.toml": _app_toml("web"),
            "apps/new/astrolift.toml": _app_toml("new"),
        },
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().scan_app_manifests(info, source_repo="acme/monorepo")

    by_name = {a.name: a for a in result.apps}
    assert by_name["web"].already_registered is True
    assert by_name["new"].already_registered is False


def test_scan_fetch_failed_without_connection(info, org, permission_resolver):
    # No source connection → ok=False with an operator-facing error.
    permission_resolver.grant(Permission.APP_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().scan_app_manifests(info, source_repo="acme/monorepo")
    assert result.ok is False
    assert result.apps == []
    assert "source connection" in (result.error or "")
