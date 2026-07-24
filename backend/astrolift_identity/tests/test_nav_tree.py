"""Tests for the astrolift_nav_tree resolver."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_registry.models import RegisteredApp
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Engineering", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="API", slug="api")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello",
        provisioning_status="ready",
    )
    return org, team, project, app


def test_nav_tree_returns_org_team_project_app():
    org, _team, _project, _app = _scaffold()

    with tenant_context(TenantContext(organization_id=org.id)):
        tree = IdentityQuery().astrolift_nav_tree(_info())

    assert tree is not None
    assert tree.organization.slug == "acme"
    assert len(tree.teams) == 1
    assert tree.teams[0].team.slug == "eng"
    assert len(tree.teams[0].projects) == 1
    assert tree.teams[0].projects[0].project.slug == "api"
    assert len(tree.teams[0].projects[0].apps) == 1
    assert tree.teams[0].projects[0].apps[0].slug == "hello"
    assert tree.teams[0].projects[0].apps[0].status == "ready"


def test_nav_tree_classifies_primitives():
    """Each app is classified into a nav primitive from its workloads:
    agent wins → single-kind → bundle. Agents link by their workload slug."""
    from astrolift_registry.models import Workload

    org, team, project, app = _scaffold()  # "hello" — 0 workloads → app

    agent_app = RegisteredApp.objects.create(
        organization=org, team=team, project=project,
        name="EMR Bug Triage", slug="agent-emr", provisioning_status="ready",
    )
    Workload.objects.create(
        registered_app=agent_app, name="emr-bug-triage", slug="emr-bug-triage",
        kind=Workload.Kind.AGENT,
    )

    wf_app = RegisteredApp.objects.create(
        organization=org, team=team, project=project,
        name="Pipeline", slug="pipeline", provisioning_status="ready",
    )
    Workload.objects.create(
        registered_app=wf_app, name="wf", slug="wf", kind=Workload.Kind.WORKFLOW,
    )

    bundle_app = RegisteredApp.objects.create(
        organization=org, team=team, project=project,
        name="Bundle", slug="bundle", provisioning_status="ready",
    )
    Workload.objects.create(
        registered_app=bundle_app, name="web", slug="web", kind=Workload.Kind.DEPLOYMENT,
    )
    Workload.objects.create(
        registered_app=bundle_app, name="cron", slug="cron", kind=Workload.Kind.CRONJOB,
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        tree = IdentityQuery().astrolift_nav_tree(_info())

    by_slug = {a.slug: (a.primitive_kind, a.primitive_slug) for a in tree.teams[0].projects[0].apps}
    assert by_slug["hello"] == ("app", "hello")
    assert by_slug["agent-emr"] == ("agent", "emr-bug-triage")  # links by workload slug
    assert by_slug["pipeline"] == ("workflow", "pipeline")
    assert by_slug["bundle"] == ("bundle", "bundle")


def test_nav_tree_returns_none_when_org_missing():
    """Tenant context with no resolvable org row should yield None
    rather than fabricate an empty tree -- that lets the UI fall back
    to its loading state instead of rendering a stub org card."""

    with tenant_context(TenantContext(organization_id=99999)):
        tree = IdentityQuery().astrolift_nav_tree(_info())

    assert tree is None


def test_nav_tree_groups_empty_team_with_no_projects():
    """A team with no projects must still appear, so the operator can
    drill in and add the first project."""
    org = Organization.objects.create(name="Acme", slug="acme")
    Team.objects.create(organization=org, name="Platform", slug="platform")

    with tenant_context(TenantContext(organization_id=org.id)):
        tree = IdentityQuery().astrolift_nav_tree(_info())

    assert tree is not None
    assert len(tree.teams) == 1
    assert tree.teams[0].team.slug == "platform"
    assert tree.teams[0].projects == []
    assert tree.teams[0].unassigned_apps == []


def test_nav_tree_excludes_apps_from_other_orgs():
    """Cross-tenant isolation: an app in a sibling org must never
    appear in this viewer's tree, even if slugs collide."""
    org_a, _team, _project, _app = _scaffold()
    org_b = Organization.objects.create(name="Beta", slug="beta")
    team_b = Team.objects.create(organization=org_b, name="Eng", slug="eng")
    project_b = Project.objects.create(organization=org_b, team=team_b, name="API", slug="api")
    RegisteredApp.objects.create(
        organization=org_b,
        team=team_b,
        project=project_b,
        name="Other",
        slug="other",
        provisioning_status="ready",
    )

    with tenant_context(TenantContext(organization_id=org_a.id)):
        tree = IdentityQuery().astrolift_nav_tree(_info())

    assert tree is not None
    slugs = {
        app.slug
        for team_node in tree.teams
        for project_node in team_node.projects
        for app in project_node.apps
    }
    assert slugs == {"hello"}


def test_nav_tree_requires_tenant_context():
    """The resolver decorator should refuse anonymous calls outright
    instead of leaking the full schema-default empty tree."""
    from core.decorators import TenantRequired

    with pytest.raises(TenantRequired):
        IdentityQuery().astrolift_nav_tree(_info())
