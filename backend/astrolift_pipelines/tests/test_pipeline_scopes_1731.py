"""Scoped pipeline collection and object regressions for #1731."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_pipelines.models import Pipeline
from astrolift_pipelines.schema.queries import PipelinesQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    return Organization.objects.create(name="Pipeline Scope Org", slug="pipeline-scope-org")


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _app(org, suffix: str):
    team = Team.objects.create(organization=org, name=f"Team {suffix}", slug=f"team-{suffix}")
    project = Project.objects.create(
        organization=org, team=team, name=f"Project {suffix}", slug=f"project-{suffix}"
    )
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {suffix}",
        slug=f"app-{suffix}",
        provisioning_status=RegisteredApp.ProvisioningStatus.READY,
    )


def _pipeline(org, name: str, app=None):
    return Pipeline.objects.create(
        organization=org,
        registered_app=app,
        name=name,
        repo_url="https://github.com/calliope/example",
        default_branch="main",
    )


def test_pipeline_collection_filters_to_app_scope(org, permission_resolver):
    own = _app(org, "own")
    sibling = _app(org, "sibling")
    _pipeline(org, "own-pipeline", own)
    _pipeline(org, "sibling-pipeline", sibling)
    _pipeline(org, "unassigned-pipeline")

    permission_resolver.deny(Permission.APP_READ)
    permission_resolver.grant(
        Permission.APP_READ,
        scope=PermissionScope(kind=ScopeKind.APP, id=own.pk),
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        rows = PipelinesQuery().astrolift_pipelines(_info())

    assert [row.name for row in rows] == ["own-pipeline"]


def test_unassigned_pipeline_requires_org_scope_with_selected_context(org, permission_resolver):
    own = _app(org, "selected")
    unassigned = _pipeline(org, "unassigned-pipeline")
    permission_resolver.deny(Permission.APP_READ)
    permission_resolver.grant(
        Permission.APP_READ,
        scope=PermissionScope(kind=ScopeKind.APP, id=own.pk),
    )
    with tenant_context(TenantContext(organization_id=org.id, project_id=own.project_id)):
        with pytest.raises(PermissionDenied):
            PipelinesQuery().astrolift_pipeline(_info(), id=str(unassigned.guid))
