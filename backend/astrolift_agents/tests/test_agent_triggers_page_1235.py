"""agentTriggersPage — cursor pagination over an agent's inbound triggers
(#1235).

``agentTriggers`` is unbounded: every ``WorkflowWebhook`` bound to the agent
comes back in one response, and an SCM fan-out accumulates bindings faster
than the run-spec editor's Trigger card can render them.

``WorkflowWebhook`` is a plain ``models.Model`` — no ``guid`` column — so the
seek key is ``(-created_at, -slug)``. ``slug`` is ``unique=True`` and NOT
NULL, which is exactly what the keyset tiebreak requires; a nullable or
non-unique tiebreak would make rows churn between pages.

Both fields are built from one queryset builder (``_agent_triggers_qs``), so
the org scope and the agent resolution cannot drift between them.

Real Postgres, no DB mocks; resolvers are invoked directly inside a bound
tenant context (the agents-test convention, see
``test_agent_trigger_bind_mutation.py``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from graphql import GraphQLError

from astrolift_agents.models import WorkflowWebhook
from astrolift_agents.schema.queries import AgentsQuery, _agent_triggers_qs
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False, is_superuser=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Trigger Org", slug="trigger-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Trigger Other", slug="trigger-other")


def _agent(org, *, app_slug="trigger-app", workload_slug="trigger-agent") -> Workload:
    """A ``kind: agent`` Workload under a fresh app in ``org``.

    ``workload_slug`` is overridable but defaults to the same value in every
    org on purpose: ``Workload.slug`` is unique per *app*, not globally, so
    two orgs can own an agent with the identical slug — which is what makes
    the cross-org test bite.
    """
    team = Team.objects.create(organization=org, name=f"T-{app_slug}", slug=f"t-{app_slug}")
    project = Project.objects.create(organization=org, team=team, name=f"P-{app_slug}", slug=f"p-{app_slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=app_slug.replace("-", " ").title(),
        slug=app_slug,
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name=workload_slug.replace("-", " ").title(),
        slug=workload_slug,
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
        run_mode=Workload.RunMode.TRIGGER.value,
    )


def _hook(agent, org, slug, *, scm_repo="", branch_pattern="", enabled=True) -> WorkflowWebhook:
    return WorkflowWebhook.objects.create(
        agent_definition=agent,
        organization=org,
        slug=slug,
        secret_hash="x" * 64,
        scm_repo=scm_repo,
        branch_pattern=branch_pattern,
        enabled=enabled,
    )


def _page(info, org, agent_slug, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id)):
        return AgentsQuery().agent_triggers_page(info, org_id=str(org.guid), agent_slug=agent_slug, **kwargs)


def _walk(info, org, agent_slug, *, limit: int, **kwargs) -> list[str]:
    """Page through the whole stream, returning every trigger slug in order."""
    slugs: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(50):  # bounded so a non-terminating walk fails loudly
            page = AgentsQuery().agent_triggers_page(
                info,
                org_id=str(org.guid),
                agent_slug=agent_slug,
                limit=limit,
                after=cursor,
                **kwargs,
            )
            slugs.extend(item.slug for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return slugs
    raise AssertionError("walk did not terminate")


def _db_order(agent, **filters) -> list[str]:
    """The seek key's own ordering, straight from Postgres."""
    return list(
        WorkflowWebhook.objects.filter(agent_definition=agent, **filters)
        .order_by("-created_at", "-slug")
        .values_list("slug", flat=True)
    )


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def test_walk_covers_every_trigger_exactly_once_and_terminates(permission_resolver, info, org):
    """Every binding is reachable, exactly once, in the seek key's order.
    Rows written in a tight loop share a ``created_at``, so this exercises
    the ``slug`` tiebreak rather than dodging it."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    for n in range(137):
        _hook(agent, org, f"hook-{n:03d}")

    slugs = _walk(info, org, agent.slug, limit=25)

    assert len(slugs) == 137
    assert len(set(slugs)) == 137, "a row was served twice"
    assert slugs == _db_order(agent)


def test_walk_terminates_when_the_last_page_is_exactly_full(permission_resolver, info, org):
    """End-of-stream comes from the overfetched row, not from
    ``len(items) < limit`` — a final page that lands exactly on the limit
    must still report ``nextCursor: null`` or the client loops forever."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    for n in range(10):
        _hook(agent, org, f"hook-{n}")

    first = _page(info, org, agent.slug, limit=5)
    assert len(first.items) == 5
    assert first.next_cursor is not None

    second = _page(info, org, agent.slug, limit=5, after=first.next_cursor)
    assert len(second.items) == 5
    assert second.next_cursor is None


def test_garbage_cursor_restarts_instead_of_erroring(permission_resolver, info, org):
    """A bookmarked / truncated token is a UX event, not a 400."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    for n in range(4):
        _hook(agent, org, f"hook-{n}")

    page = _page(info, org, agent.slug, limit=10, after="not-a-real-cursor")

    assert [i.slug for i in page.items] == _db_order(agent)


# ---------------------------------------------------------------------------
# total_count
# ---------------------------------------------------------------------------


def test_total_count_is_the_whole_result_set_not_the_page(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    for n in range(12):
        _hook(agent, org, f"hook-{n:02d}")

    page = _page(info, org, agent.slug, limit=5)

    assert len(page.items) == 5
    assert page.total_count == 12


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_matches_slug_scm_repo_and_branch_pattern(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    _hook(agent, org, "nightly-sweep", scm_repo="acme/platform", branch_pattern="main")
    _hook(agent, org, "release-guard", scm_repo="acme/website", branch_pattern="release/*")
    _hook(agent, org, "adhoc", scm_repo="", branch_pattern="")

    assert [i.slug for i in _page(info, org, agent.slug, search="nightly").items] == ["nightly-sweep"]
    assert [i.slug for i in _page(info, org, agent.slug, search="website").items] == ["release-guard"]
    assert [i.slug for i in _page(info, org, agent.slug, search="release/").items] == ["release-guard"]


def test_search_narrows_total_count_not_just_the_page(permission_resolver, info, org):
    """A count that ignored the search would render "9 results" over a
    one-row table."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    _hook(agent, org, "keep-me", scm_repo="acme/platform")
    for n in range(8):
        _hook(agent, org, f"other-{n}", scm_repo="acme/website")

    page = _page(info, org, agent.slug, search="keep-me")

    assert [i.slug for i in page.items] == ["keep-me"]
    assert page.total_count == 1


def test_search_walk_is_still_complete(permission_resolver, info, org):
    """Search + cursor compose: the seek clause must not drop matches once
    the filter is in play."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    for n in range(40):
        _hook(agent, org, f"keep-{n:02d}", scm_repo="acme/platform")
    for n in range(10):
        _hook(agent, org, f"drop-{n:02d}", scm_repo="acme/website")

    slugs = _walk(info, org, agent.slug, limit=7, search="keep-")

    assert len(slugs) == 40
    assert slugs == _db_order(agent, slug__startswith="keep-")


# ---------------------------------------------------------------------------
# Tenancy + row-set parity with the deprecated list field
# ---------------------------------------------------------------------------


def test_other_orgs_triggers_are_invisible_in_items_and_total(permission_resolver, info, org, other_org):
    """Both orgs own an agent with the SAME slug. Without the org clause on
    the agent lookup, the caller would page another tenant's bindings."""
    permission_resolver.grant(Permission.AGENT_READ)
    mine = _agent(org)
    theirs = _agent(other_org, app_slug="their-app")
    assert mine.slug == theirs.slug, "precondition: the slug collides across orgs"
    _hook(mine, org, "ours")
    _hook(theirs, other_org, "theirs-1")
    _hook(theirs, other_org, "theirs-2")

    page = _page(info, org, mine.slug, limit=50)

    assert [i.slug for i in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked the other org's rows"


def test_triggers_bound_to_another_agent_in_the_same_org_are_excluded(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    sibling = _agent(org, app_slug="sibling-app", workload_slug="sibling-agent")
    _hook(agent, org, "mine")
    _hook(sibling, org, "not-mine")

    page = _page(info, org, agent.slug, limit=50)

    assert [i.slug for i in page.items] == ["mine"]
    assert page.total_count == 1


def test_unknown_agent_slug_yields_an_empty_page(permission_resolver, info, org):
    """Deny-by-default: an unresolvable agent must produce no rows AND a
    zero count — never the org's whole webhook table."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    _hook(agent, org, "real")

    page = _page(info, org, "no-such-agent", limit=50)

    assert page.items == []
    assert page.total_count == 0
    assert page.next_cursor is None


def test_disabled_triggers_are_still_listed(permission_resolver, info, org):
    """Parity with the list field: the Trigger card renders unbound rows so
    the operator can see (and re-enable) a disabled binding."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    _hook(agent, org, "live")
    _hook(agent, org, "retired", enabled=False)

    page = _page(info, org, agent.slug, limit=50)

    assert sorted(i.slug for i in page.items) == ["live", "retired"]
    assert {i.slug: i.enabled for i in page.items} == {"live": True, "retired": False}


def test_foreign_org_id_argument_is_rejected(permission_resolver, info, org, other_org):
    """``_caller_org_id`` RAISES on a mismatched ``orgId`` — the page field
    must keep that gate, not silently fall back to the tenant's org."""
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    _hook(agent, org, "ours")

    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(GraphQLError, match="organization mismatch"):
            AgentsQuery().agent_triggers_page(info, org_id=str(other_org.guid), agent_slug=agent.slug)


def test_page_and_deprecated_list_field_agree_on_the_row_set(permission_resolver, info, org, other_org):
    """Both surfaces are built from ``_agent_triggers_qs``, so the org scope
    and agent resolution cannot drift apart. This pins it."""
    permission_resolver.grant(Permission.AGENT_READ)
    mine = _agent(org)
    theirs = _agent(other_org, app_slug="their-app")
    for n in range(7):
        _hook(mine, org, f"hook-{n}")
    _hook(mine, org, "disabled-one", enabled=False)
    _hook(theirs, other_org, "foreign")

    with tenant_context(TenantContext(organization_id=org.id)):
        listed = AgentsQuery().agent_triggers(info, org_id=str(org.guid), agent_slug=mine.slug)

    assert [i.slug for i in listed] == _walk(info, org, mine.slug, limit=3)


def test_no_tenant_context_is_refused_outright(permission_resolver, info, org):
    """Fails closed rather than paging every org's triggers (#1183).

    ``@tenant_scoped`` rejects first; ``_caller_org_id`` raising on a null
    tenant is defence in depth behind it."""
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent(org)
    _hook(agent, org, "secret")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            AgentsQuery().agent_triggers_page(info, org_id=str(org.guid), agent_slug=agent.slug)


def test_builder_matches_nothing_for_a_foreign_org_pk(org, other_org):
    """The deny-by-default half: even called directly with another org's pk,
    the builder resolves no agent and returns an empty queryset — never the
    foreign tenant's bindings."""
    mine = _agent(org)
    _hook(mine, org, "ours")

    assert _agent_triggers_qs(other_org.id, agent_slug=mine.slug).count() == 0
    assert _agent_triggers_qs(org.id, agent_slug=mine.slug).count() == 1
