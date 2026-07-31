"""workflowsPage / workflowDefinitionsPage — cursor pagination (#1235).

Both list fields they replace were *unbounded*: ``workflows`` returned
every Workflow an org owns and ``workflowDefinitions`` every template
visible to it, in one response. That is the other half of #1230 — the
200-row cap makes rows unreachable, no cap at all makes the response
unbounded — and these tests are what stop either coming back.

``workflowDefinitionsPage`` also changes the sort order on purpose (the
list field's ``organization_id`` leading key is NULL on every
platform-global template, so it cannot be a keyset sort field); the walk
tests below pin the new order and prove no row is lost to it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db.models import Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from astrolift_workflows.schema.queries import WorkflowsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from workflows.models import Workflow, WorkflowDefinition

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Page Org A", slug="wfpage-org-a")


@pytest.fixture
def other_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Page Org B", slug="wfpage-org-b")


def _make_def(slug, *, organization=None, name=None):
    """A tier-1 definition with no stages — ``Workflow.save`` only rejects
    *unbound agent* stages, so a stage-less definition is the cheapest
    valid parent for the tier-2 rows these tests page over."""
    return WorkflowDefinition.objects.create(
        name=name if name is not None else f"Def {slug}",
        slug=slug,
        organization=organization,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
        is_enabled=True,
    )


def _make_wf(org, definition, slug, *, name=None, description=""):
    return Workflow.objects.create(
        organization=org,
        definition=definition,
        name=name if name is not None else f"WF {slug}",
        slug=slug,
        description=description,
    )


def _walk_workflows(query, org, *, limit, max_pages=100, **kwargs):
    """Page through workflowsPage, returning every guid served, in order."""
    guids: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(max_pages):  # bounded so a non-terminating walk fails loudly
            page = query.workflows_page(_info(), limit=limit, after=cursor, **kwargs)
            guids.extend(item.guid for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return guids
    raise AssertionError("workflowsPage walk did not terminate")


def _walk_definitions(query, org, *, limit, max_pages=100, **kwargs):
    guids: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(max_pages):
            page = query.workflow_definitions_page(_info(), limit=limit, after=cursor, **kwargs)
            guids.extend(item.guid for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return guids
    raise AssertionError("workflowDefinitionsPage walk did not terminate")


def _db_workflow_order(org):
    return [
        str(g)
        for g in Workflow.objects.filter(organization=org, deleted_at__isnull=True)
        .order_by("-created_at", "-guid")
        .values_list("guid", flat=True)
    ]


# ---------------------------------------------------------------------------
# workflowsPage
# ---------------------------------------------------------------------------


def test_workflows_walk_covers_every_row_exactly_once(org, permission_resolver):
    """The whole stream is reachable across pages — no row served twice,
    none lost at a page boundary. Rows written in a loop share a
    ``created_at`` to the microsecond, so this exercises the guid
    tiebreak rather than a clean timestamp ordering."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("wfpage-walk", organization=org)
    for n in range(25):
        _make_wf(org, d, f"walk-{n:02d}")

    guids = _walk_workflows(WorkflowsQuery(), org, limit=4)

    assert len(guids) == 25
    assert len(set(guids)) == 25, "a workflow was served on two pages"
    assert guids == _db_workflow_order(org), "the walk drifted from the DB's own ordering"


def test_workflows_walk_terminates_when_a_page_lands_on_the_last_row(org, permission_resolver):
    """End-of-stream comes from the overfetched row, not from a short
    page: 8 rows at limit=4 must stop after two pages, not serve an
    empty third one forever."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("wfpage-exact", organization=org)
    for n in range(8):
        _make_wf(org, d, f"exact-{n:02d}")

    query = WorkflowsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        first = query.workflows_page(_info(), limit=4)
        second = query.workflows_page(_info(), limit=4, after=first.next_cursor)

    assert len(first.items) == 4
    assert first.next_cursor is not None
    assert len(second.items) == 4
    assert second.next_cursor is None


def test_workflows_total_count_is_the_whole_result_set(org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("wfpage-total", organization=org)
    for n in range(12):
        _make_wf(org, d, f"total-{n:02d}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflows_page(_info(), limit=5)

    assert len(page.items) == 5
    assert page.total_count == 12


def test_workflows_page_serves_the_same_rows_as_the_deprecated_list_field(org, permission_resolver):
    """Both fields build on one queryset builder, so their notion of "a
    Workflow row" cannot drift. (Order is compared as a set: the list
    field orders on ``-created_at`` alone, which is not a total order
    when rows share a timestamp.)"""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("wfpage-parity", organization=org)
    for n in range(6):
        _make_wf(org, d, f"parity-{n:02d}")

    query = WorkflowsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        listed = {w.guid for w in query.workflows(_info())}
    walked = set(_walk_workflows(query, org, limit=2))

    assert walked == listed
    assert len(listed) == 6


def test_workflows_page_excludes_soft_deleted_rows(org, permission_resolver):
    """``Workflow.objects`` is not soft-delete aware — the filter lives in
    the shared builder, and it has to survive the conversion."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("wfpage-deleted", organization=org)
    live = _make_wf(org, d, "live-wf")
    gone = _make_wf(org, d, "gone-wf")
    Workflow.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflows_page(_info(), limit=50)

    assert [i.guid for i in page.items] == [str(live.guid)]
    assert page.total_count == 1


def test_workflows_of_another_org_are_invisible(org, other_org, permission_resolver):
    """``Workflow.organization`` is a real FK but the manager is not
    tenant-aware, so the scope is the resolver's job (#1183). The count
    has to be scoped too — a leaky count renders "9 results" over a
    3-row table and tells the caller how much another org has."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    ours_def = _make_def("wfpage-ours", organization=org)
    theirs_def = _make_def("wfpage-theirs", organization=other_org)
    ours = [_make_wf(org, ours_def, f"ours-{n}") for n in range(3)]
    theirs = [_make_wf(other_org, theirs_def, f"theirs-{n}") for n in range(4)]

    guids = _walk_workflows(WorkflowsQuery(), org, limit=2)
    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflows_page(_info(), limit=50)

    assert set(guids) == {str(w.guid) for w in ours}
    assert not {str(w.guid) for w in theirs} & set(guids)
    assert page.total_count == 3, "the count leaked the other org's workflows"


def test_workflows_search_narrows_total_count_not_just_the_page(org, permission_resolver):
    """A count that ignored ``search`` would render "3 results" over a
    one-row table."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    alpha = _make_def("wfpage-alpha", organization=org, name="Alpha Definition")
    beta = _make_def("wfpage-beta", organization=org, name="Beta Definition")
    backup = _make_wf(org, alpha, "nightly-backup", name="Nightly backup")
    gate = _make_wf(org, alpha, "deploy-gate", name="Deploy gate")
    cost = _make_wf(org, beta, "cost-report", name="Cost report", description="monthly spend rollup")

    query = WorkflowsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        unfiltered = query.workflows_page(_info(), limit=50)
        by_name = query.workflows_page(_info(), search="nightly", limit=1)
        by_slug = query.workflows_page(_info(), search="deploy-gate", limit=1)
        by_description = query.workflows_page(_info(), search="rollup", limit=1)
        by_definition = query.workflows_page(_info(), search="Beta Definition", limit=1)

    assert unfiltered.total_count == 3
    assert [i.guid for i in by_name.items] == [str(backup.guid)]
    assert by_name.total_count == 1
    assert [i.guid for i in by_slug.items] == [str(gate.guid)]
    assert [i.guid for i in by_description.items] == [str(cost.guid)]
    assert [i.guid for i in by_definition.items] == [str(cost.guid)]
    assert by_definition.total_count == 1


def test_workflows_search_survives_the_page_boundary(org, permission_resolver):
    """The seek clause is applied on top of the search filter, not
    instead of it — a second page must not widen back to every row."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("wfpage-search-walk", organization=org)
    matching = [_make_wf(org, d, f"keep-{n:02d}", name=f"Keeper {n:02d}") for n in range(5)]
    for n in range(5):
        _make_wf(org, d, f"drop-{n:02d}", name=f"Other {n:02d}")

    guids = _walk_workflows(WorkflowsQuery(), org, limit=2, search="Keeper")

    assert set(guids) == {str(w.guid) for w in matching}


def test_workflows_page_without_tenant_context_is_refused_outright(org, permission_resolver):
    """Fails closed rather than paging every org's workflows (#1183).
    ``@tenant_scoped`` rejects first; the resolver's own ``ok=False``
    branch is defence in depth behind it."""
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("wfpage-notenant", organization=org)
    _make_wf(org, d, "no-tenant")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            WorkflowsQuery().workflows_page(_info(), limit=50)


def test_workflows_page_refuses_a_foreign_org_id_argument(org, other_org, permission_resolver):
    """``orgId`` is verified against the tenant, not trusted: another
    org's guid returns an empty page with a zero count, never their
    rows (#1042 deny-by-default)."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    theirs_def = _make_def("wfpage-foreign", organization=other_org)
    _make_wf(other_org, theirs_def, "foreign-wf")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflows_page(_info(), org_id=str(other_org.guid), limit=50)

    assert page.items == []
    assert page.total_count == 0
    assert page.next_cursor is None


# ---------------------------------------------------------------------------
# workflowDefinitionsPage
# ---------------------------------------------------------------------------


def _db_definition_order(org, *, search=None):
    """The order the page must reproduce.

    ``search`` mirrors the resolver's own filter so a test can scope both
    sides to the rows it created — migration 0006 seeds a platform-global
    catalogue into every test database, and ``visible_to_org`` returns
    those too.
    """
    qs = WorkflowDefinition.visible_to_org(org.id).filter(deleted_at__isnull=True)
    if search:
        qs = qs.filter(
            Q(name__icontains=search) | Q(slug__icontains=search) | Q(description__icontains=search)
        )
    return [
        str(g)
        for g in qs.annotate(sort_name=Coalesce("name", Value("")))
        .order_by("sort_name", "guid")
        .values_list("guid", flat=True)
    ]


def test_definitions_walk_is_name_ascending_and_covers_every_row(org, permission_resolver):
    """The deliberate ordering change: name A→Z with a guid tiebreak,
    replacing ``(organization_id, name)``. Two rows deliberately share a
    name so the tiebreak carries a page boundary."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    for n in range(9):
        _make_def(f"defpage-own-{n:02d}", organization=org, name=f"Def {n:02d}")
    _make_def("defpage-shared-own", organization=org, name="Shared Name")
    _make_def("defpage-shared-global", organization=None, name="Shared Name")

    guids = _walk_definitions(WorkflowsQuery(), org, limit=2, search="defpage-")

    assert len(guids) == 11
    assert len(set(guids)) == 11, "a definition was served on two pages"
    assert guids == _db_definition_order(org, search="defpage-")


def test_definitions_walk_includes_platform_globals(org, permission_resolver):
    """Spec 40 §2.1's read scope survives the conversion: own UNION
    global. A page that dropped the globals would empty the template
    catalogue for every org that has authored none of its own."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    mine = _make_def("defpage-mine", organization=org, name="Mine")
    theirs_global = _make_def("defpage-global", organization=None, name="Global Template")

    guids = _walk_definitions(WorkflowsQuery(), org, limit=1, search="defpage-")

    assert set(guids) == {str(mine.guid), str(theirs_global.guid)}
    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflow_definitions_page(_info(), limit=50, search="defpage-")
    globals_seen = [i.guid for i in page.items if i.is_global]
    assert globals_seen == [str(theirs_global.guid)]
    assert page.total_count == 2


def test_definitions_total_count_is_the_whole_result_set(org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    for n in range(7):
        _make_def(f"defpage-total-{n:02d}", organization=org, name=f"Total {n:02d}")
    for n in range(3):
        _make_def(f"defpage-total-g-{n:02d}", organization=None, name=f"Global {n:02d}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflow_definitions_page(_info(), limit=4, search="defpage-total")

    assert len(page.items) == 4
    assert page.total_count == 10


def test_definitions_page_serves_the_same_rows_as_the_deprecated_list_field(org, permission_resolver):
    """One shared builder — the page and the list field disagree on
    ORDER, by design, but never on membership."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    for n in range(5):
        _make_def(f"defpage-parity-{n:02d}", organization=org, name=f"Parity {n:02d}")
    _make_def("defpage-parity-global", organization=None, name="Parity global")

    query = WorkflowsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        # The list field has no search argument, so narrow its result
        # to this test's rows on the way out instead.
        listed = {d.guid for d in query.workflow_definitions(_info()) if d.slug.startswith("defpage-parity")}
    walked = set(_walk_definitions(query, org, limit=2, search="defpage-parity"))

    assert walked == listed
    assert len(listed) == 6


def test_definitions_page_excludes_soft_deleted_rows(org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    live = _make_def("defpage-live", organization=org, name="Live")
    gone = _make_def("defpage-gone", organization=org, name="Gone")
    WorkflowDefinition.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflow_definitions_page(_info(), limit=50, search="defpage-")

    assert [i.guid for i in page.items] == [str(live.guid)]
    assert page.total_count == 1


def test_definitions_of_another_org_are_invisible(org, other_org, permission_resolver):
    """An org sees its own + the platform globals. Another tenant's
    templates appear in neither the items nor the count — a definition
    name is business intelligence (it names their pipeline stages)."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    mine = _make_def("defpage-x-mine", organization=org, name="Mine")
    shared = _make_def("defpage-x-global", organization=None, name="Global")
    theirs = [
        _make_def(f"defpage-x-theirs-{n}", organization=other_org, name=f"Theirs {n}") for n in range(4)
    ]

    guids = _walk_definitions(WorkflowsQuery(), org, limit=1, search="defpage-x-")
    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflow_definitions_page(_info(), limit=50, search="defpage-x-")

    assert set(guids) == {str(mine.guid), str(shared.guid)}
    assert not {str(d.guid) for d in theirs} & set(guids)
    assert page.total_count == 2, "the count leaked the other org's definitions"


def test_definitions_search_narrows_total_count_not_just_the_page(org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    a = _make_def("defpage-s-alpha", organization=org, name="Alpha pipeline")
    b = _make_def("defpage-s-beta", organization=None, name="Beta pipeline")
    _make_def("defpage-s-gamma", organization=org, name="Gamma release")

    query = WorkflowsQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        unfiltered = query.workflow_definitions_page(_info(), limit=50)
        matched = query.workflow_definitions_page(_info(), search="pipeline", limit=1)
        by_slug = query.workflow_definitions_page(_info(), search="defpage-s-gamma", limit=50)

    assert unfiltered.total_count == 3
    assert len(matched.items) == 1
    assert matched.total_count == 2, "the count ignored the search term"
    assert set(_walk_definitions(query, org, limit=1, search="pipeline")) == {
        str(a.guid),
        str(b.guid),
    }
    assert [i.slug for i in by_slug.items] == ["defpage-s-gamma"]


def test_definition_with_a_null_name_stays_reachable(org, permission_resolver):
    """``WorkflowDefinition.name`` is nullable, and a NULL in the seek key
    is unreachable-row territory: NULLs sort last in Postgres and compare
    as neither ``<`` nor ``=``, so ``name > 'last'`` would drop them and
    truncate the walk. The resolver sorts on a coalesced copy instead —
    this is the regression test for that."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    named = [_make_def(f"defpage-null-{n}", organization=org, name=f"Named {n}") for n in range(3)]
    unnamed = WorkflowDefinition.objects.create(
        name=None,
        slug="defpage-unnamed",
        organization=org,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
    )
    WorkflowDefinition.objects.filter(pk=unnamed.pk).update(name=None)
    unnamed.refresh_from_db()
    assert unnamed.name is None, "precondition: the row really is NULL-named"

    guids = _walk_definitions(WorkflowsQuery(), org, limit=1)

    assert str(unnamed.guid) in guids, "the NULL-named row fell off the end of the walk"
    assert set(guids) == {str(d.guid) for d in named} | {str(unnamed.guid)}
    assert guids == _db_definition_order(org)


def test_definitions_page_without_tenant_context_is_refused_outright(org, permission_resolver):
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.WORKFLOW_READ)
    _make_def("defpage-notenant", organization=org, name="No tenant")
    _make_def("defpage-notenant-global", organization=None, name="No tenant global")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            WorkflowsQuery().workflow_definitions_page(_info(), limit=50)


def test_definitions_page_refuses_a_foreign_org_id_argument(org, other_org, permission_resolver):
    """Deny-by-default matters more here than on workflowsPage: the
    definitions builder starts from ``visible_to_org``, and an unguarded
    ``visible_to_org(None)`` would hand back every platform-global
    template. A rejected orgId returns nothing at all."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    _make_def("defpage-foreign-theirs", organization=other_org, name="Theirs")
    _make_def("defpage-foreign-global", organization=None, name="A Global")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = WorkflowsQuery().workflow_definitions_page(_info(), org_id=str(other_org.guid), limit=50)

    assert page.items == []
    assert page.total_count == 0
    assert page.next_cursor is None
