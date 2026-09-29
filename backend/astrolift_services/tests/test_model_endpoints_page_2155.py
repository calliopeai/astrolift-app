"""The Models page on the list contract, a single endpoint read, and deployedBy (#2155)."""

from __future__ import annotations

import pytest

from astrolift_graphql import UnsupportedSort
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.models import ManagedService
from astrolift_services.schema.queries import ServicesQuery
from astrolift_services.schema.types import ModelEndpointsFilterInput
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


def _model(
    name, *, app=None, project=None, cluster=None, variant="vllm", status="active", by=None, kind=None
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
        kind=kind or ManagedService.Kind.MODEL_ENDPOINT,
        variant=variant,
        name=name,
        status=status,
        config={"model": "m"},
        created_by=by,
    )


@pytest.fixture
def world():
    w = ScopeWorld("me2155")
    w.user = make_user("me2155")
    w.cluster = make_cluster(w, "me2155")
    w.medops_llm = _model("medops-llm", app=w.medops_app, cluster=w.cluster, by=w.user)
    w.platform_llm = _model(
        "platform-llm", app=w.platform_app, cluster=w.cluster, variant="bedrock", status="failed"
    )
    w.shared_llm = _model("shared-llm", project=w.platform_project, cluster=w.cluster)
    _model("medops-cache", app=w.medops_app, cluster=w.cluster, kind=ManagedService.Kind.REDIS, variant="")
    other = ScopeWorld("me2155b")
    w.foreign = _model("other-org-llm", app=other.medops_app, cluster=make_cluster(other, "me2155b"))
    return w


def _org_reader(w):
    bind_role(
        w.user,
        permissions=[Permission.APP_READ, Permission.PROJECT_READ],
        kind="ORG",
        scope_id=w.org.pk,
        slug="me2155-org",
    )


def _page(w, **kwargs):
    with as_tenant(w, w.user):
        return ServicesQuery().astrolift_model_endpoints_page(make_info(w.user), **kwargs)


def _names(page):
    return [row.name for row in page.items]


def test_page_is_numbered_sorted_and_carries_deployed_by(world):
    _org_reader(world)
    page = _page(world, page=1, page_size=2)
    assert _names(page) == ["medops-llm", "platform-llm"]
    assert (page.total_count, page.page, page.page_size) == (3, 1, 2)
    assert page.items[0].deployed_by_email == "reba-me2155@acme.test"
    assert page.items[0].deployed_by_me is True
    assert page.items[1].deployed_by_email == ""
    assert _names(_page(world, page=2, page_size=2)) == ["shared-llm"]
    assert _names(_page(world, sort="-name")) == ["shared-llm", "platform-llm", "medops-llm"]
    with pytest.raises(UnsupportedSort):
        _page(world, sort="config")


def test_page_filters_and_search(world):
    _org_reader(world)
    f = ModelEndpointsFilterInput
    assert _names(_page(world, filter=f(status=["failed"]))) == ["platform-llm"]
    assert _names(_page(world, filter=f(variant=["BEDROCK"]))) == ["platform-llm"]
    assert _names(_page(world, filter=f(owner_scope=["project"]))) == ["shared-llm"]
    assert _names(_page(world, filter=f(owner_scope=["app"]))) == ["medops-llm", "platform-llm"]
    assert _names(_page(world, filter=f(app=[world.medops_app.slug]))) == ["medops-llm"]
    assert _names(_page(world, filter=f(project=[world.platform_project.slug]))) == [
        "platform-llm",
        "shared-llm",
    ]
    assert _names(_page(world, filter=f(cluster=[world.cluster.slug]))) == [
        "medops-llm",
        "platform-llm",
        "shared-llm",
    ]
    assert _names(_page(world, filter=f(deployed_by=["me"]))) == ["medops-llm"]
    assert _names(_page(world, search="gateway")) == ["platform-llm"]
    assert _names(_page(world, search="other-org")) == []


def test_page_and_single_read_follow_the_callers_bindings(world):
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.PROJECT_READ],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="me2155-team",
    )
    assert _names(_page(world)) == ["medops-llm"]
    with as_tenant(world, world.user):
        q = ServicesQuery()
        assert (
            q.astrolift_model_endpoint(make_info(world.user), id=str(world.medops_llm.guid)).name
            == "medops-llm"
        )
        assert q.astrolift_model_endpoint(make_info(world.user), id=str(world.platform_llm.guid)) is None


def test_single_read_never_crosses_orgs_or_kinds(world):
    _org_reader(world)
    cache = ManagedService.objects.get(name="medops-cache")
    with as_tenant(world, world.user):
        q = ServicesQuery()
        assert q.astrolift_model_endpoint(make_info(world.user), id=str(world.shared_llm.guid)) is not None
        assert q.astrolift_model_endpoint(make_info(world.user), id=str(world.foreign.guid)) is None
        assert q.astrolift_model_endpoint(make_info(world.user), id=str(cache.guid)) is None
        assert q.astrolift_model_endpoint(make_info(world.user), id="not-a-guid") is None
