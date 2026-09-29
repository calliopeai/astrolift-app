"""``workflowDefinitionRunsPage`` and ``workflowDefinitionRun`` (#2155).

Cursor paging, search and filters on the definition runs list, and a
single read by guid so a run page can open a workflow run. Both stay in
the caller's org and its run visibility.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_graphql import UnsupportedSort
from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.schema.queries import WorkflowsQuery
from astrolift_workflows.schema.workflow_config_types import WorkflowDefinitionRunsFilterInput
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx
from workflows.models import WorkflowDefinition

User = get_user_model()
pytestmark = pytest.mark.django_db


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


def _definition(org, slug, project=None):
    return WorkflowDefinition.objects.create(
        name=f"Def {slug}",
        slug=slug,
        organization=org,
        project=project,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
        is_enabled=True,
    )


def _run(org, definition, name, *, status=WorkflowRun.Status.RUNNING, by=None, trigger="manual"):
    return WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"WorkflowDefinitionRunWorkflow-{name}",
        run_id=f"temporal-{name}",
        status=status,
        trigger_actor_user=by,
        trigger_kind=trigger,
    )


@pytest.fixture
def world():
    viewer = User.objects.create_user(username="wdr-viewer", email="wdr-v@t.com", password="pw")
    other_user = User.objects.create_user(username="wdr-other", email="wdr-o@t.com", password="pw")
    org = Organization.objects.create(name="WDR", slug="wdr-org")
    team = Team.objects.create(organization=org, name="Eng", slug="wdr-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="wdr-demo")
    build = _definition(org, "wdr-build", project)
    review = _definition(org, "wdr-review")
    r1 = _run(org, build, "one", by=viewer)
    r2 = _run(org, build, "two", status=WorkflowRun.Status.FAILED, by=other_user, trigger="api")
    r3 = _run(org, review, "three", status=WorkflowRun.Status.COMPLETED, trigger="schedule")
    foreign_org = Organization.objects.create(name="WDR B", slug="wdr-org-b")
    foreign = _run(foreign_org, _definition(foreign_org, "wdr-build-b"), "foreign")
    return SimpleNamespace(
        viewer=viewer,
        other_user=other_user,
        org=org,
        r1=r1,
        r2=r2,
        r3=r3,
        foreign=foreign,
        foreign_org=foreign_org,
    )


def _page(w, **kwargs):
    with _tenant_ctx(TenantContext(organization_id=w.org.id, actor_user_id=w.viewer.id)):
        return WorkflowsQuery().workflow_definition_runs_page(_info(w.viewer), **kwargs)


def _guids(page):
    return [item.guid for item in page.items]


def test_page_walks_newest_first_with_a_cursor(permission_resolver, world):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    first = _page(world, limit=2)
    assert _guids(first) == [str(world.r3.guid), str(world.r2.guid)]
    assert first.total_count == 3 and first.next_cursor
    rest = _page(world, limit=2, after=first.next_cursor)
    assert _guids(rest) == [str(world.r1.guid)] and rest.next_cursor is None
    oldest = _page(world, sort="created")
    assert _guids(oldest) == [str(world.r1.guid), str(world.r2.guid), str(world.r3.guid)]
    with pytest.raises(UnsupportedSort):
        _page(world, sort="-startedAt")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"search": "wdr-review"}, ["r3"]),
        ({"search": "WorkflowDefinitionRunWorkflow-tw"}, ["r2"]),
        ({"search": "wdr-demo"}, ["r2", "r1"]),
        ({"filter": WorkflowDefinitionRunsFilterInput(status=["failed", "completed"])}, ["r3", "r2"]),
        ({"filter": WorkflowDefinitionRunsFilterInput(definition=["WDR-BUILD"])}, ["r2", "r1"]),
        ({"filter": WorkflowDefinitionRunsFilterInput(project=["wdr-demo"])}, ["r2", "r1"]),
        ({"filter": WorkflowDefinitionRunsFilterInput(trigger=["schedule"])}, ["r3"]),
        ({"filter": WorkflowDefinitionRunsFilterInput(started_by=["me"])}, ["r1"]),
        ({"filter": WorkflowDefinitionRunsFilterInput(started_by_me=True)}, ["r1"]),
        ({"filter": WorkflowDefinitionRunsFilterInput(started_by_me=False)}, ["r3", "r2"]),
    ],
)
def test_page_search_and_filters(permission_resolver, world, kwargs, expected):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    page = _page(world, **kwargs)
    assert _guids(page) == [str(getattr(world, name).guid) for name in expected]
    assert page.total_count == len(expected)


def test_page_started_by_a_user_id(permission_resolver, world):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    page = _page(world, filter=WorkflowDefinitionRunsFilterInput(started_by=[str(world.other_user.pk)]))
    assert _guids(page) == [str(world.r2.guid)]


def test_page_never_lists_another_orgs_runs(permission_resolver, world):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    page = _page(world, search="WorkflowDefinitionRunWorkflow-foreign")
    assert page.items == [] and page.total_count == 0
    foreign = _page(world, org_id=str(world.foreign_org.guid))
    assert foreign.items == [] and foreign.total_count == 0


def test_single_run_reads_one_of_the_callers_runs(permission_resolver, world):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    with _tenant_ctx(TenantContext(organization_id=world.org.id, actor_user_id=world.viewer.id)):
        query = WorkflowsQuery()
        row = query.workflow_definition_run(_info(world.viewer), guid=str(world.r2.guid))
        assert row is not None
        assert (row.definition_slug, row.status, row.trigger_kind) == ("wdr-build", "failed", "api")
        assert query.workflow_definition_run(_info(world.viewer), guid=str(world.foreign.guid)) is None
        assert query.workflow_definition_run(_info(world.viewer), guid="not-a-guid") is None
        assert query.workflow_definition_run(_info(world.viewer), guid=str(uuid.uuid4())) is None
        assert (
            query.workflow_definition_run(
                _info(world.viewer), guid=str(world.r1.guid), org_id=str(world.foreign_org.guid)
            )
            is None
        )
