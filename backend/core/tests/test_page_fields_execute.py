"""Every ``…Page`` field resolves through the real GraphQL schema (#1235).

The per-app page tests all invoke resolvers directly — ``LifecycleQuery()
.astrolift_deployments_page(info, …)`` — which constructs the Query class
itself. That is fast and precise, and it is blind to one whole class of
failure: anything that depends on how Strawberry *binds* a root resolver.

Every conversion extracts a shared ``_<name>_qs(...)`` builder so the list
field and its Page sibling cannot drift apart about what a row is. Those
builders are module-level functions rather than methods for a reason this
suite exists to hold: Strawberry binds a ROOT resolver's ``self`` to the
schema's root value, which the Django view leaves ``None``, so reaching a
helper through ``self`` raises ``AttributeError`` on every real request
while every direct-invocation test stays green. That regression shipped
once during this epic and was invisible to 300 passing tests.

So this executes real documents against ``config.schema``. It asserts no
GraphQL errors and a well-formed envelope — deliberately not row contents,
which the per-app suites already cover in depth.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from config.schema import schema
from core.permissions import Permission
from core.schema.context import StrawberryContext
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()


class _FakeRequest:
    def __init__(self, user):
        self.user = user
        self.session = {}
        self.headers = {}


def _ctx(user) -> StrawberryContext:
    return StrawberryContext(_FakeRequest(user))


# Every Page field reachable without first creating a parent row. Fields
# with a mandatory id/slug argument — astroliftPipelineRunsPage(pipelineId:),
# astroliftAppDeployTokensPage(appSlug:), agentTriggersPage(agentSlug:),
# astroliftAppTeamAccessesPage(appSlug:), astroliftWebhookDeliveriesPage,
# astroliftManagedServicesPage, astroliftEventsAggregatedPage — are covered
# by their own app's suite, which can build the parent row first.
PAGE_FIELDS = [
    "astroliftDeploymentsPage",
    "astroliftTaskRunsPage",
    "astroliftAgentRunsPage",
    "astroliftScheduledJobRunsPage",
    "astroliftCommandRunsPage",
    "astroliftPreviewEnvironmentsPage",
    "astroliftMembersPage",
    "astroliftRoleBindingsPage",
    "astroliftInvitationsPage",
    "astroliftRolesPage",
    "astroliftApiTokensPage",
    "astroliftTeamsPage",
    "astroliftProjectsPage",
    "astroliftPoliciesPage",
    "astroliftClustersPage",
    "astroliftWorkloadsPage",
    "astroliftAlertRulesPage",
    "astroliftAlertEventsPage",
    "astroliftWebhookSubscriptionsPage",
    "astroliftPipelinesPage",
    "astroliftSourceConnectionsPage",
    "astroliftSshDeployKeysPage",
    "astroliftEventsPage",
    "astroliftAuditEventsPage",
    "workflowsPage",
    "workflowDefinitionsPage",
]


def _page_type_has_total_count(field: str) -> bool:
    query_type = schema.as_str()  # force the schema to build before introspecting
    del query_type
    graphql_query = schema._schema.query_type
    page_type = graphql_query.fields[field].type
    while hasattr(page_type, "of_type"):
        page_type = page_type.of_type
    return "totalCount" in page_type.fields


@pytest.fixture
def org_user(db):
    org = Organization.objects.create(name="ExecTest", slug="exec-test")
    user = User.objects.create(username="exec@test", email="exec@test")
    return org, user


@pytest.mark.parametrize("field", PAGE_FIELDS)
def test_page_field_executes_through_the_schema(field, org_user, permission_resolver):
    """`self` must be the Query instance, not the (None) root value.

    If Strawberry bound `self` to the root value, every conversion that
    calls `self._<name>_qs(...)` would raise
    `AttributeError: 'NoneType' object has no attribute '_…_qs'`
    on the first real request while every unit test stayed green.
    """
    org, user = org_user
    for permission in Permission:
        permission_resolver.grant(permission)

    # Ask the schema what this page type carries. The three hand-rolled
    # page types that predate the shared helper have their own shapes
    # (AstroliftEventPage has `reason` and no `totalCount`), and their
    # wire contract was deliberately preserved through the conversion.
    selection = "items { __typename } nextCursor"
    if _page_type_has_total_count(field):
        selection += " totalCount"

    document = f"{{ {field}(limit: 2) {{ {selection} }} }}"
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = schema.execute_sync(document, context_value=_ctx(user))

    assert result.errors is None, f"{field} failed to execute: {result.errors}"
    payload = result.data[field]
    assert isinstance(payload["items"], list)
    # Not every field is empty for a fresh org — the role and workflow
    # catalogues carry platform-global rows, and creating the org emits
    # its own audit events — so assert the envelope invariants that hold
    # for all of them rather than a row count.
    # totalCount is nullable: astroliftAuditEventsPage computes it only
    # behind its opt-in `includeTotal` flag, a contract older than this
    # epic and deliberately preserved.
    if payload.get("totalCount") is not None:
        assert payload["totalCount"] >= len(payload["items"])
    assert len(payload["items"]) <= 2, "limit was not honoured"
    if payload["nextCursor"] is not None:
        # A cursor promises more rows, which only makes sense off a full page.
        assert len(payload["items"]) == 2, f"{field} handed back a cursor mid-page"


def test_page_field_returns_rows_and_a_cursor_through_the_schema(org_user, permission_resolver):
    """The empty-org cases above would still pass if the seek clause were
    broken, so walk a populated stream end to end over the wire."""
    from astrolift_operations.models import Event

    org, user = org_user
    for permission in Permission:
        permission_resolver.grant(permission)

    Event.objects.bulk_create(
        [Event(event_type="exec.test", payload={"n": n}, organization=org) for n in range(5)]
    )

    document = """
      query Page($after: String) {
        astroliftEventsPage(limit: 2, after: $after) { items { id } nextCursor }
      }
    """

    seen: list[str] = []
    cursor = None
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        for _ in range(10):
            result = schema.execute_sync(
                document, variable_values={"after": cursor}, context_value=_ctx(user)
            )
            assert result.errors is None, result.errors
            page = result.data["astroliftEventsPage"]
            seen.extend(item["id"] for item in page["items"])
            cursor = page["nextCursor"]
            if cursor is None:
                break

    assert cursor is None, "walk did not terminate over the wire"
    assert len(seen) == 5
    assert len(set(seen)) == 5, "a row was served twice"


def test_deprecated_list_field_still_executes(org_user, permission_resolver):
    """The list fields stay on the contract for the CLI and mobile app; a
    @deprecated directive must not change that they resolve."""
    org, user = org_user
    for permission in Permission:
        permission_resolver.grant(permission)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = schema.execute_sync("{ astroliftDeployments(limit: 5) { id } }", context_value=_ctx(user))

    assert result.errors is None, result.errors
    assert result.data["astroliftDeployments"] == []
