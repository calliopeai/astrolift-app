"""astroliftPipelinesPage / astroliftPipelineRunsPage — cursor pagination (#1235).

Both list fields these replace end at a hard slice: ``astroliftPipelines``
at 500 rows, ``astroliftPipelineRuns`` at 200. Neither offers a way to
reach the row after the cap, so on a pipeline that runs per-commit the
pipeline's own history leaves the UI within weeks — the operator-visible
bug behind #1230.

The two pages deliberately keep the seek keys the list fields already
ordered on (``name`` ascending for the catalogue, ``-run_number`` for the
run stream) rather than the helper's default ``-created_at``, so adopting
the page is not also a silent re-sort. These tests pin that, the tenant
boundary, and the guid tiebreak — which is load-bearing here because
neither ``(organization, name)`` nor ``(pipeline, run_number)`` carries a
DB uniqueness constraint.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun
from astrolift_pipelines.schema.queries import PipelinesQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme Corp", slug="acme-corp")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Rival Inc", slug="rival-inc")


@pytest.fixture(autouse=True)
def _granted(permission_resolver):
    """Every resolver here gates on APP_READ; grant it once."""
    permission_resolver.grant(Permission.APP_READ)
    return permission_resolver


def _pipeline(org, name, **kwargs):
    return Pipeline.objects.create(
        organization=org,
        name=name,
        repo_url=kwargs.pop("repo_url", "https://github.com/acme/app"),
        default_branch=kwargs.pop("default_branch", "main"),
        **kwargs,
    )


def _run(pipeline, run_number, **kwargs):
    return PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=run_number,
        trigger_kind=kwargs.pop("trigger_kind", PipelineRun.TriggerKind.PUSH),
        trigger_ref=kwargs.pop("trigger_ref", "refs/heads/main"),
        trigger_actor=kwargs.pop("trigger_actor", "ci-bot"),
        **kwargs,
    )


def _walk_pipelines(query, org, *, limit, **kwargs):
    """Page through the whole catalogue, returning every name in order."""
    names: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(40):  # bounded so a non-terminating walk fails loudly
            page = query.astrolift_pipelines_page(_info(), limit=limit, after=cursor, **kwargs)
            names.extend(item.name for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return names
    raise AssertionError("walk did not terminate")


def _walk_runs(query, org, *, pipeline_id, limit, **kwargs):
    """Page through one pipeline's runs, returning every run number in order."""
    numbers: list[int] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(40):
            page = query.astrolift_pipeline_runs_page(
                _info(), pipeline_id=pipeline_id, limit=limit, after=cursor, **kwargs
            )
            numbers.extend(item.run_number for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return numbers
    raise AssertionError("walk did not terminate")


# ---------------------------------------------------------------------------
# astroliftPipelinesPage
# ---------------------------------------------------------------------------


def test_pipelines_page_reaches_past_the_old_five_hundred_row_cap(org):
    """The regression that motivated the epic: with the list field, the
    501st pipeline did not exist as far as the UI was concerned."""
    Pipeline.objects.bulk_create(
        [
            Pipeline(
                organization=org,
                name=f"pipe-{n:03d}",
                repo_url="https://github.com/acme/app",
                default_branch="main",
                toml_path=f".astrolift/pipelines/pipe-{n:03d}.toml",
            )
            for n in range(505)
        ]
    )

    query = PipelinesQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = query.astrolift_pipelines(_info(), limit=1000)
    assert len(capped) == 500, "precondition: the list field still caps"

    names = _walk_pipelines(query, org, limit=50)
    assert len(names) == 505
    assert len(set(names)) == 505, "a row was served twice"


def test_pipelines_walk_is_alphabetical_and_loses_nothing(org):
    """The page keeps the list field's catalogue ordering — adopting it
    must not re-sort the table under the operator."""
    for name in ("zeta-deploy", "alpha-ci", "mid-build", "beta-release", "omega-nightly"):
        _pipeline(org, name)

    expected = list(
        Pipeline.objects.filter(organization=org).order_by("name", "guid").values_list("name", flat=True)
    )
    assert _walk_pipelines(PipelinesQuery(), org, limit=2) == expected


def test_pipelines_guid_tiebreak_serves_duplicate_names_exactly_once(org):
    """Name uniqueness is a mutation-level CONFLICT check, not a DB
    constraint, so equal sort values are reachable. Without the guid
    tiebreak the cursor for the first of them would re-serve it forever."""
    for _ in range(3):
        _pipeline(org, "same-name")

    expected = [
        str(g)
        for g in Pipeline.objects.filter(organization=org)
        .order_by("name", "guid")
        .values_list("guid", flat=True)
    ]

    seen: list[str] = []
    cursor: str | None = None
    query = PipelinesQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(10):
            page = query.astrolift_pipelines_page(_info(), limit=1, after=cursor)
            seen.extend(str(item.id) for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        else:
            raise AssertionError("walk did not terminate")

    assert seen == expected


def test_pipelines_total_count_is_the_whole_result_set(org):
    for n in range(12):
        _pipeline(org, f"pipe-{n:02d}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipelines_page(_info(), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_pipelines_search_matches_name_repo_and_bound_app(org):
    from astrolift_identity.models import Team
    from astrolift_registry.models import RegisteredApp

    team = Team.objects.create(organization=org, name="Platform", slug="platform")
    app = RegisteredApp.objects.create(organization=org, team=team, name="Billing API", slug="billing-api")

    _pipeline(org, "nightly-build", repo_url="https://github.com/acme/infra")
    _pipeline(org, "release-train", repo_url="https://gitlab.com/acme/widgets")
    _pipeline(org, "deploy-it", repo_url="https://github.com/acme/other", registered_app=app)

    query = PipelinesQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_name = query.astrolift_pipelines_page(_info(), search="nightly")
        by_repo = query.astrolift_pipelines_page(_info(), search="gitlab.com")
        by_app_slug = query.astrolift_pipelines_page(_info(), search="billing-api")

    assert [p.name for p in by_name.items] == ["nightly-build"]
    assert [p.name for p in by_repo.items] == ["release-train"]
    assert [p.name for p in by_app_slug.items] == ["deploy-it"]


def test_pipelines_search_narrows_total_count_not_just_the_page(org):
    """A count that ignored the search would render "3 results" over a
    one-row table."""
    _pipeline(org, "keep-me")
    _pipeline(org, "other-one")
    _pipeline(org, "other-two")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipelines_page(_info(), search="keep-me")
    assert [p.name for p in page.items] == ["keep-me"]
    assert page.total_count == 1


def test_pipelines_search_over_the_app_join_does_not_duplicate_rows(org):
    """``registered_app`` is joined for search; a many-valued join would
    fan one pipeline into several rows and inflate total_count."""
    from astrolift_identity.models import Team
    from astrolift_registry.models import RegisteredApp

    team = Team.objects.create(organization=org, name="Platform", slug="platform")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name="Widget Service", slug="widget-service"
    )
    _pipeline(org, "widget-deploy", repo_url="https://github.com/acme/widget", registered_app=app)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipelines_page(_info(), search="widget")
    assert [p.name for p in page.items] == ["widget-deploy"]
    assert page.total_count == 1


def test_soft_deleted_pipelines_are_not_paged(org):
    """Both fields share one queryset builder, so the soft-delete filter
    cannot drift between them — this pins it on the page side."""
    _pipeline(org, "live-one")
    gone = _pipeline(org, "dead-one")
    gone.soft_delete()

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipelines_page(_info(), limit=50)
    assert [p.name for p in page.items] == ["live-one"]
    assert page.total_count == 1


def test_other_orgs_pipelines_are_invisible(org, other_org):
    """Pipeline's manager is not tenant-aware and ``@tenant_scoped`` only
    asserts a tenant exists, so the org clause is the resolver's job
    (#1183) — in the rows AND in the count."""
    _pipeline(org, "ours")
    _pipeline(other_org, "aaa-theirs")  # sorts first, so a leak is unmissable

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipelines_page(_info(), limit=50)
    assert [p.name for p in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked the other org's row"


def test_pipelines_page_without_tenant_context_is_refused_outright(org):
    """Fails closed rather than paging every org's catalogue (#1183).

    ``@tenant_scoped`` rejects first; ``_pipelines_qs`` filtering on
    ``organization_id=None`` (a NOT NULL column) matches nothing if it is
    ever reached. Both have to hold — this pins the outer one.
    """
    from core.decorators import TenantRequired

    _pipeline(org, "ours")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            PipelinesQuery().astrolift_pipelines_page(_info(), limit=50)


def test_pipelines_page_denies_without_the_permission(org, permission_resolver):
    from core.permissions import PermissionDenied

    permission_resolver.deny(Permission.APP_READ)
    _pipeline(org, "ours")

    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            PipelinesQuery().astrolift_pipelines_page(_info(), limit=50)


# ---------------------------------------------------------------------------
# astroliftPipelineRunsPage
# ---------------------------------------------------------------------------


def test_runs_page_reaches_past_the_old_two_hundred_row_cap(org):
    pipeline = _pipeline(org, "busy-pipeline")
    PipelineRun.objects.bulk_create(
        [
            PipelineRun(
                pipeline=pipeline,
                run_number=n,
                trigger_kind=PipelineRun.TriggerKind.PUSH,
                trigger_ref="refs/heads/main",
                trigger_actor="ci-bot",
                status=PipelineRun.Status.SUCCESS,
            )
            for n in range(1, 206)
        ]
    )

    query = PipelinesQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = query.astrolift_pipeline_runs(_info(), pipeline_id=str(pipeline.guid), limit=1000)
    assert len(capped) == 200, "precondition: the list field still caps"

    numbers = _walk_runs(query, org, pipeline_id=str(pipeline.guid), limit=50)
    assert len(numbers) == 205
    assert len(set(numbers)) == 205, "a row was served twice"
    assert numbers[0] == 205, "newest run first"


def test_runs_walk_is_newest_first_and_loses_nothing(org):
    pipeline = _pipeline(org, "steady-pipeline")
    for n in (3, 1, 5, 2, 4, 7, 6):
        _run(pipeline, n)

    expected = list(
        PipelineRun.objects.filter(pipeline=pipeline)
        .order_by("-run_number", "-guid")
        .values_list("run_number", flat=True)
    )
    assert _walk_runs(PipelinesQuery(), org, pipeline_id=str(pipeline.guid), limit=3) == expected


def test_runs_guid_tiebreak_serves_equal_run_numbers_exactly_once(org):
    """``(pipeline, run_number)`` is indexed but not unique, so two racing
    writers can land the same number. The tiebreak is what keeps the walk
    from stalling on them."""
    pipeline = _pipeline(org, "racy-pipeline")
    for _ in range(3):
        _run(pipeline, 7)
    _run(pipeline, 6)

    expected = [
        str(g)
        for g in PipelineRun.objects.filter(pipeline=pipeline)
        .order_by("-run_number", "-guid")
        .values_list("guid", flat=True)
    ]

    seen: list[str] = []
    cursor: str | None = None
    query = PipelinesQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(10):
            page = query.astrolift_pipeline_runs_page(
                _info(), pipeline_id=str(pipeline.guid), limit=1, after=cursor
            )
            seen.extend(str(item.id) for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        else:
            raise AssertionError("walk did not terminate")

    assert seen == expected


def test_runs_total_count_is_the_whole_result_set(org):
    pipeline = _pipeline(org, "counted-pipeline")
    for n in range(1, 13):
        _run(pipeline, n)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipeline_runs_page(_info(), pipeline_id=str(pipeline.guid), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_runs_search_narrows_total_count_not_just_the_page(org):
    pipeline = _pipeline(org, "searched-pipeline")
    _run(pipeline, 1, trigger_ref="refs/heads/main", trigger_actor="ci-bot")
    _run(pipeline, 2, trigger_ref="refs/pull/42/head", trigger_actor="ana@acme.test")
    _run(pipeline, 3, trigger_ref="refs/heads/main", trigger_actor="ci-bot")

    query = PipelinesQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_ref = query.astrolift_pipeline_runs_page(_info(), pipeline_id=str(pipeline.guid), search="pull/42")
        by_actor = query.astrolift_pipeline_runs_page(_info(), pipeline_id=str(pipeline.guid), search="ana@")
        by_kind = query.astrolift_pipeline_runs_page(_info(), pipeline_id=str(pipeline.guid), search="push")

    assert [r.run_number for r in by_ref.items] == [2]
    assert by_ref.total_count == 1
    assert [r.run_number for r in by_actor.items] == [2]
    assert by_actor.total_count == 1
    assert by_kind.total_count == 3, "trigger_kind is searchable; all three are pushes"


def test_runs_of_a_sibling_pipeline_do_not_bleed_in(org):
    """``run_number`` is a per-pipeline counter, so the page is only a
    coherent stream while the pipeline filter holds."""
    mine = _pipeline(org, "mine")
    theirs = _pipeline(org, "theirs")
    for n in (1, 2, 3):
        _run(mine, n, trigger_actor="mine-bot")
        _run(theirs, n, trigger_actor="theirs-bot")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipeline_runs_page(_info(), pipeline_id=str(mine.guid), limit=50)
    assert [r.run_number for r in page.items] == [3, 2, 1]
    assert page.total_count == 3
    assert {r.trigger_actor for r in page.items} == {"mine-bot"}


def test_soft_deleted_runs_are_not_paged(org):
    pipeline = _pipeline(org, "partly-pruned")
    _run(pipeline, 1)
    gone = _run(pipeline, 2)
    gone.soft_delete()

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipeline_runs_page(
            _info(), pipeline_id=str(pipeline.guid), limit=50
        )
    assert [r.run_number for r in page.items] == [1]
    assert page.total_count == 1


def test_runs_of_another_orgs_pipeline_are_invisible(org, other_org):
    """Runs reach the org only through the pipeline FK — a caller holding
    a sibling org's pipeline guid must see nothing, count included."""
    theirs = _pipeline(other_org, "rival-pipeline")
    for n in (1, 2, 3):
        _run(theirs, n)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipeline_runs_page(_info(), pipeline_id=str(theirs.guid), limit=50)
    assert page.items == []
    assert page.total_count == 0, "the count leaked the other org's runs"


def test_runs_of_a_soft_deleted_pipeline_are_not_paged(org):
    pipeline = _pipeline(org, "retired-pipeline")
    _run(pipeline, 1)
    pipeline.soft_delete()

    with tenant_context(TenantContext(organization_id=org.id)):
        page = PipelinesQuery().astrolift_pipeline_runs_page(
            _info(), pipeline_id=str(pipeline.guid), limit=50
        )
    assert page.items == []
    assert page.total_count == 0


def test_runs_page_without_tenant_context_is_refused_outright(org):
    from core.decorators import TenantRequired

    pipeline = _pipeline(org, "ours")
    _run(pipeline, 1)

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            PipelinesQuery().astrolift_pipeline_runs_page(_info(), pipeline_id=str(pipeline.guid), limit=50)


def test_runs_page_denies_without_the_permission(org, permission_resolver):
    from core.permissions import PermissionDenied

    permission_resolver.deny(Permission.APP_READ)
    pipeline = _pipeline(org, "ours")
    _run(pipeline, 1)

    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            PipelinesQuery().astrolift_pipeline_runs_page(_info(), pipeline_id=str(pipeline.guid), limit=50)
