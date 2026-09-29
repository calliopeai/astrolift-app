"""``agentWorkloads(dispatchable: true)`` lists only what
``runAstroliftAgent`` would actually accept from the caller (#2071).

Before this, a token client had no way to tell "I can read this agent"
apart from "I can dispatch it" -- ``agentWorkloads`` was gated on
``agent.read`` regardless of ``agent.dispatch``, and ``me.modules`` answers
for the whole org, not per agent. ``dispatchable_agent_workloads`` (in
``astrolift_agents.visibility``) closes that gap: ``agent.dispatch`` RBAC
(not ``agent.read``), the same bearer-token team/share ceiling every other
org-scoped agent read applies, and Task run family only -- the same three
gates ``dispatch_registered_agent`` itself enforces.

Resolvers are exercised by direct invocation (the agents-test convention),
with the controllable ``permission_resolver`` fixture for RBAC (it patches
the ``core.permissions.granted_scopes`` provider ``workload_rows`` reads,
same as ``test_run_agent_mutation.py``) and a real ``ApiToken`` row for the
token-ceiling case (mirrors ``test_fleet_scopes_1745.py``).
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Organization, Team
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


@pytest.fixture
def info():
    """Minimal ``info``-shaped object (mirrors ``test_run_agent_mutation.py``)."""
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Dispatchable Org", slug="dispatchable-org")


@pytest.fixture
def user():
    return get_user_model().objects.create(
        username="dispatchable-user", email="dispatchable-user@astrolift.dev"
    )


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _agent_workload(
    org, *, app_slug="triage-app", workload_slug="triage", run_family=Workload.RunFamily.TASK
):
    team = Team.objects.create(organization=org, name=f"T-{app_slug}", slug=f"t-{app_slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=app_slug.replace("-", " ").title(),
        slug=app_slug,
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name=workload_slug.replace("-", " ").title(),
        slug=workload_slug,
        kind=Workload.Kind.AGENT,
        run_family=run_family,
    )


@contextmanager
def _token(org, user, *, team=None, scopes=("admin",)):
    credential = ApiToken.objects.create(
        user=user,
        organization=org,
        team=team,
        name="dispatchable-token",
        token_hash=uuid4().hex * 2,
        token_last_4="test",
        scopes=list(scopes),
    )
    marker = set_current_api_token(credential)
    try:
        yield credential
    finally:
        reset_current_api_token(marker)


def _slugs(rows):
    return {row.slug for row in rows}


def test_dispatchable_false_is_the_unfiltered_default(permission_resolver, info, org, with_tenant_org):
    """Omitting ``dispatchable`` keeps today's behavior: ``agent.read``
    alone is enough, exactly as before #2071."""
    permission_resolver.grant(Permission.AGENT_READ)
    workload = _agent_workload(org)
    with with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info, org_id=str(org.guid))
    assert _slugs(rows) == {workload.slug}


def test_dispatchable_true_excludes_an_agent_the_caller_can_read_but_not_dispatch(
    permission_resolver, info, org, with_tenant_org
):
    """The core proof (#2071): a caller who can read but not dispatch sees
    the agent in the plain list, and it is absent from the narrowed one."""
    permission_resolver.grant(Permission.AGENT_READ)
    workload = _agent_workload(org)
    with with_tenant_org(org):
        plain = AgentsQuery().agent_workloads(info, org_id=str(org.guid))
        narrowed = AgentsQuery().agent_workloads(info, org_id=str(org.guid), dispatchable=True)
    assert _slugs(plain) == {workload.slug}
    assert narrowed == []


def test_dispatchable_true_includes_an_agent_the_caller_may_dispatch(
    permission_resolver, info, org, with_tenant_org
):
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)
    with with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info, org_id=str(org.guid), dispatchable=True)
    assert _slugs(rows) == {workload.slug}


def test_dispatchable_true_excludes_a_service_family_agent(permission_resolver, info, org, with_tenant_org):
    """Nothing ever dispatches a Service-family agent --
    ``dispatch_registered_agent`` refuses it outright -- so the dispatchable
    list agrees even though RBAC is otherwise wide open."""
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    service = _agent_workload(
        org, app_slug="svc-app", workload_slug="svc", run_family=Workload.RunFamily.SERVICE
    )
    with with_tenant_org(org):
        plain = AgentsQuery().agent_workloads(info, org_id=str(org.guid))
        narrowed = AgentsQuery().agent_workloads(info, org_id=str(org.guid), dispatchable=True)
    assert _slugs(plain) == {service.slug}
    assert narrowed == []


def test_dispatchable_true_respects_the_bearer_token_ceiling(
    permission_resolver, info, org, user, with_tenant_org
):
    """An org-wide ``agent.dispatch`` grant is still capped by the bearer
    token's own scopes (#2071's "token ceiling included"): a token scoped
    to ``read:apps`` cannot dispatch, so the narrowed list is empty even
    though the session RBAC would allow it (mirrors
    ``test_fleet_scopes_1745.test_bearer_permission_ceiling_still_applies``).
    """
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _agent_workload(org)
    with _token(org, user, scopes=("read:apps",)), with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info, org_id=str(org.guid), dispatchable=True)
    assert rows == []


def test_dispatchable_true_allows_a_token_scoped_to_dispatch(
    permission_resolver, info, org, user, with_tenant_org
):
    """The inverse of the ceiling test: an ``admin``-scoped token (the
    fixture default used throughout the fleet-scope suite) does not narrow
    the list beyond what RBAC already allows."""
    permission_resolver.grant(Permission.AGENT_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)
    with _token(org, user, scopes=("admin",)), with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info, org_id=str(org.guid), dispatchable=True)
    assert _slugs(rows) == {workload.slug}
