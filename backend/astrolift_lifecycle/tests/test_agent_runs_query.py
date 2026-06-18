"""Tests for the astrolift_agent_runs resolver (spec 33 PR-2).

Covers the ``project_slug`` filter added for the per-project Agents
detail surface, and the org-scoping the same change introduces — the
resolver previously applied no organization filter, so ``app_slug`` /
``project_slug`` (both unique only *within* a tenant) would otherwise
read another org's runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AgentRun
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold(org_slug, project_slug, app_slug):
    org = Organization.objects.create(name=org_slug.title(), slug=org_slug)
    team = Team.objects.create(organization=org, name=f"{org_slug} team", slug=f"{org_slug}-team")
    project = Project.objects.create(organization=org, team=team, name=project_slug.title(), slug=project_slug)
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=app_slug.title(),
        slug=app_slug,
        provisioning_status="ready",
    )
    workload = Workload.objects.create(
        registered_app=app, name="Agent", slug="agent", kind=Workload.Kind.AGENT
    )
    return org, project, app, workload


def test_agent_runs_filtered_by_project(permission_resolver):
    org, project, app, workload = _scaffold("acme", "alpha", "app-a")
    # A second project + agent in the SAME org.
    team_b = Team.objects.create(organization=org, name="beta team", slug="beta-team")
    project_b = Project.objects.create(organization=org, team=team_b, name="Beta", slug="beta")
    app_b = RegisteredApp.objects.create(
        organization=org, team=team_b, project=project_b, name="App B", slug="app-b",
        provisioning_status="ready",
    )
    workload_b = Workload.objects.create(
        registered_app=app_b, name="Agent B", slug="agent-b", kind=Workload.Kind.AGENT
    )
    permission_resolver.grant(Permission.APP_READ)
    run_a = AgentRun.objects.create(workload=workload, status=AgentRun.Status.RUNNING)
    AgentRun.objects.create(workload=workload_b, status=AgentRun.Status.RUNNING)

    with tenant_context(TenantContext(organization_id=org.id)):
        rows = LifecycleQuery().astrolift_agent_runs(_info(), project_slug="alpha")

    assert len(rows) == 1
    assert str(rows[0].id) == str(run_a.guid)


def test_agent_runs_org_scoped(permission_resolver):
    """A caller in org A must not see org B's runs even when both orgs
    reuse the same app/project slugs."""
    org_a, _, _, workload_a = _scaffold("org-a", "shared", "shared-app")
    org_b, _, _, workload_b = _scaffold("org-b", "shared", "shared-app")
    permission_resolver.grant(Permission.APP_READ)
    run_a = AgentRun.objects.create(workload=workload_a, status=AgentRun.Status.RUNNING)
    AgentRun.objects.create(workload=workload_b, status=AgentRun.Status.RUNNING)

    # Passing org B's app_slug while scoped to org A must NOT leak B's run.
    with tenant_context(TenantContext(organization_id=org_a.id)):
        rows_by_app = LifecycleQuery().astrolift_agent_runs(_info(), app_slug="shared-app")
        rows_by_project = LifecycleQuery().astrolift_agent_runs(_info(), project_slug="shared")

    assert {str(r.id) for r in rows_by_app} == {str(run_a.guid)}
    assert {str(r.id) for r in rows_by_project} == {str(run_a.guid)}


def test_agent_runs_unfiltered_is_still_org_scoped(permission_resolver):
    """With no slug filter the resolver returns the caller-org's runs
    only — the org scope is unconditional, not gated on a slug arg."""
    org_a, _, _, workload_a = _scaffold("org-a", "alpha", "app-a")
    org_b, _, _, workload_b = _scaffold("org-b", "beta", "app-b")
    permission_resolver.grant(Permission.APP_READ)
    run_a = AgentRun.objects.create(workload=workload_a, status=AgentRun.Status.SUCCEEDED)
    AgentRun.objects.create(workload=workload_b, status=AgentRun.Status.SUCCEEDED)

    with tenant_context(TenantContext(organization_id=org_a.id)):
        rows = LifecycleQuery().astrolift_agent_runs(_info())

    assert {str(r.id) for r in rows} == {str(run_a.guid)}


def test_agent_runs_project_and_status_compose(permission_resolver):
    org, project, app, workload = _scaffold("acme", "alpha", "app-a")
    permission_resolver.grant(Permission.APP_READ)
    running = AgentRun.objects.create(workload=workload, status=AgentRun.Status.RUNNING)
    AgentRun.objects.create(workload=workload, status=AgentRun.Status.SUCCEEDED)

    with tenant_context(TenantContext(organization_id=org.id)):
        rows = LifecycleQuery().astrolift_agent_runs(_info(), project_slug="alpha", status="running")

    assert {str(r.id) for r in rows} == {str(running.guid)}
