"""The Models page lists every model endpoint the caller can read (#2040)."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.models import ManagedService
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


def _model(
    name, *, app=None, project=None, cluster=None, kind=ManagedService.Kind.MODEL_ENDPOINT, variant="vllm"
):
    env = None
    if app is not None:
        env, _ = AppEnvironment.objects.get_or_create(
            registered_app=app, name="prod", defaults={"tenant_cluster": cluster}
        )
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        project=project,
        tenant_cluster=cluster if project is not None else None,
        kind=kind,
        variant=variant,
        name=name,
        config={"model": "m"},
    )


@pytest.fixture
def world():
    w = ScopeWorld("me2040")
    w.user = make_user("me2040")
    cluster = make_cluster(w, "me2040")
    _model("medops-llm", app=w.medops_app, cluster=cluster)
    _model("platform-llm", app=w.platform_app, cluster=cluster, variant="bedrock")
    _model("shared-llm", project=w.platform_project, cluster=cluster)
    _model("medops-cache", app=w.medops_app, cluster=cluster, kind=ManagedService.Kind.REDIS, variant="")
    other = ScopeWorld("me2040b")
    _model("other-org-llm", app=other.medops_app, cluster=make_cluster(other, "me2040b"))
    return w


def _names(w):
    with as_tenant(w, w.user):
        return [s.name for s in ServicesQuery().astrolift_model_endpoints(make_info(w.user))]


def test_an_org_reader_sees_every_model_in_its_org_and_nothing_else(world):
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.PROJECT_READ],
        kind="ORG",
        scope_id=world.org.pk,
        slug="me2040-org",
    )
    assert _names(world) == ["medops-llm", "platform-llm", "shared-llm"]


def test_a_team_reader_sees_only_its_teams_models(world):
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.PROJECT_READ],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="me2040-team",
    )
    assert _names(world) == ["medops-llm"]
