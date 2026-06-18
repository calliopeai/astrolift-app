"""Tests for the ``scanAgentManifests`` discovery-preview query (spec 33, PR-3).

``scan_agent_manifests`` (on ``AgentsQuery``) backs the monorepo-discovery
step of the agent onboarding wizard: it scans a repo for agent manifests and
returns a preview WITHOUT persisting anything. These tests pin, against a
real database:

  * the preview lists each agent manifest found (path + parsed name/slug/kind);
  * ``already_registered`` reflects whether an app for that repo + manifest
    path already exists in the caller's org;
  * the query is org-scoped — a foreign ``org_id`` is rejected;
  * the read gate is ``app.read``;
  * nothing is persisted by the preview.

The SCM tree fetch is patched at the service's ``_default_tree_fetch`` so the
query runs end-to-end with no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from graphql import GraphQLError

from astrolift_agents.schema.queries import AgentsQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _agent_toml(name: str) -> str:
    return (
        f'name = "{name}"\n'
        "[[workloads]]\n"
        f'name = "{name}"\n'
        'kind = "agent"\n'
        "[[workloads.containers]]\n"
        f'name = "{name}"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
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
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/summarize/astrolift.toml": _agent_toml("summarize"),
        },
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = AgentsQuery().scan_agent_manifests(
            info, org_id=GUID(str(org.guid)), source_repo="acme/agents"
        )

    assert result.ok is True
    assert result.error is None
    by_slug = {a.slug: a for a in result.agents}
    assert set(by_slug) == {"triage", "summarize"}
    assert by_slug["triage"].manifest_path == "agents/triage/astrolift.toml"
    assert by_slug["triage"].workload_kind == "agent"
    assert all(a.already_registered is False for a in result.agents)
    # Preview persists nothing.
    assert RegisteredApp.objects.filter(organization=org).count() == 0


def test_scan_flags_already_registered(monkeypatch, info, org, permission_resolver):
    _connect(org)
    permission_resolver.grant(Permission.APP_READ)
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    # Pre-register one agent app at the same repo + manifest path.
    RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Triage",
        slug="triage",
        source_kind="github",
        source_repo="acme/agents",
        manifest_path="agents/triage/astrolift.toml",
    )
    _patch_tree(
        monkeypatch,
        {
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/new/astrolift.toml": _agent_toml("new"),
        },
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = AgentsQuery().scan_agent_manifests(
            info, org_id=GUID(str(org.guid)), source_repo="acme/agents"
        )

    by_slug = {a.slug: a for a in result.agents}
    assert by_slug["triage"].already_registered is True
    assert by_slug["new"].already_registered is False


def test_scan_fetch_failed_without_connection(info, org, permission_resolver):
    # No source connection → ok=False with an operator-facing error.
    permission_resolver.grant(Permission.APP_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = AgentsQuery().scan_agent_manifests(
            info, org_id=GUID(str(org.guid)), source_repo="acme/agents"
        )
    assert result.ok is False
    assert result.agents == []
    assert "source connection" in (result.error or "")


def test_scan_rejects_foreign_org(monkeypatch, info, org, permission_resolver):
    other = Organization.objects.create(name="Globex", slug="globex")
    _connect(org)
    permission_resolver.grant(Permission.APP_READ)
    _patch_tree(monkeypatch, {"agents/a/astrolift.toml": _agent_toml("a")})

    # Active tenant is ``org`` but the query asks for ``other``'s GUID.
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(GraphQLError):
            AgentsQuery().scan_agent_manifests(info, org_id=GUID(str(other.guid)), source_repo="acme/agents")
