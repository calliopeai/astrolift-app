"""Action-safety gates: on by default, strictest wins, server-enforced (#2202)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_identity.action_safety import effective, gate
from astrolift_identity.models import ActionSafetyRule, Member
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _default_on(monkeypatch):
    monkeypatch.setattr("astrolift_identity.action_safety.DEFAULT_ON", True)


@pytest.fixture
def world():
    w = ScopeWorld("safety-" + uuid4().hex[:8])
    w.user = make_user(uuid4().hex[:8])
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    return w


def rule(w, kind, scope_id, action="secret.change", **values):
    return ActionSafetyRule.objects.create(
        organization=w.org, scope_kind=kind, scope_id=scope_id, action=action, **values
    )


def test_every_gate_is_on_by_default_with_self_approval_allowed(world):
    r = effective(world.org.pk, "app.deregister", app_id=world.medops_app.pk, user_id=world.user.pk)
    assert (r.enabled, r.confirmation, r.verification, r.allow_self_approval) == (True, True, True, True)


def test_only_the_org_turns_a_gate_off_and_any_narrower_scope_can_turn_it_back_on(world):
    rule(world, "ORG", None, enabled=False)
    assert effective(world.org.pk, "secret.change").enabled is False

    rule(world, "APP", world.medops_app.pk, enabled=True)
    on_app = effective(world.org.pk, "secret.change", app_id=world.medops_app.pk)
    off_elsewhere = effective(world.org.pk, "secret.change", app_id=world.platform_app.pk)
    assert on_app.enabled is True and off_elsewhere.enabled is False
    assert on_app.sources == ("ORG", f"APP:{world.medops_app.pk}")


def test_a_narrower_scope_cannot_weaken_what_applies(world):
    rule(world, "TEAM", world.medops.pk, enabled=False)
    assert effective(world.org.pk, "secret.change", team_id=world.medops.pk).enabled is True


def test_self_approval_is_off_if_any_applicable_scope_says_so(world):
    rule(world, "USER", world.user.pk, allow_self_approval=False)
    assert effective(world.org.pk, "secret.change", user_id=world.user.pk).allow_self_approval is False
    assert effective(world.org.pk, "secret.change").allow_self_approval is True


def _info(session):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(session=session)))


def test_the_gate_refuses_an_unelevated_session_even_with_the_install_switch_off(world):
    from django.contrib.sessions.backends.db import SessionStore

    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        deny = gate(_info(SessionStore()), "secret.change", app=world.medops_app)
    assert deny is not None and deny.errors[0].code == "STEP_UP_REQUIRED"
    assert "Change a secret" in deny.errors[0].message


def test_an_org_that_turned_the_gate_off_is_not_asked_and_an_api_token_passes(world):
    from django.contrib.sessions.backends.db import SessionStore

    rule(world, "ORG", None, enabled=False)
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        assert gate(_info(SessionStore()), "secret.change", app=world.medops_app) is None
        token_call = SimpleNamespace(
            context=SimpleNamespace(request=SimpleNamespace(session=SessionStore(), _api_token=object()))
        )
        assert gate(token_call, "app.deregister", app=world.medops_app) is None


@pytest.mark.django_db(transaction=True)
def test_rules_api_refuses_a_narrower_scope_turning_a_gate_off_and_reports_what_applies(client):
    world = ScopeWorld("safety-api-" + uuid4().hex[:8])
    world.user = make_user(uuid4().hex[:8])
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        world.user,
        permissions=[Permission.ORG_READ, Permission.ORG_UPDATE],
        kind="ORG",
        scope_id=world.org.pk,
        slug=uuid4().hex,
    )
    _token, headers = http_token(world, scopes=("admin",))
    mutation = (
        "mutation($i:SetActionSafetyRuleInput!){setActionSafetyRule(input:$i){ok errors{code field} "
        "data{action scopeKind enabled}}}"
    )

    refused = graphql_http(
        client,
        headers,
        mutation,
        {
            "i": {
                "action": "secret.change",
                "scopeKind": "APP",
                "scopeId": str(world.medops_app.guid),
                "enabled": False,
            }
        },
    )["data"]["setActionSafetyRule"]
    off = graphql_http(
        client, headers, mutation, {"i": {"action": "secret.change", "scopeKind": "ORG", "enabled": False}}
    )["data"]["setActionSafetyRule"]
    applies = graphql_http(
        client,
        headers,
        "query($s:String){astroliftActionSafety(appSlug:$s){action enabled verification sources}}",
        {"s": world.medops_app.slug},
    )["data"]["astroliftActionSafety"]

    assert refused["ok"] is False and refused["errors"][0]["field"] == "enabled"
    assert off["ok"] is True and off["data"] == {
        "action": "secret.change",
        "scopeKind": "ORG",
        "enabled": False,
    }
    by_action = {row["action"]: row for row in applies}
    assert by_action["secret.change"]["enabled"] is False and by_action["secret.change"]["sources"] == ["ORG"]
    assert (
        by_action["app.deregister"]["enabled"] is True and by_action["app.deregister"]["verification"] is True
    )


def test_exec_attach_needs_a_fresh_step_up_unless_the_org_turned_it_off(world):
    import datetime as dt

    from django.utils import timezone

    from astrolift_identity.action_safety import session_needs_step_up
    from astrolift_identity.session_elevation import SESSION_KEY_ELEVATED_UNTIL

    stale = {}
    fresh = {SESSION_KEY_ELEVATED_UNTIL: (timezone.now() + dt.timedelta(minutes=5)).isoformat()}
    org = world.org.pk
    assert session_needs_step_up(org, "exec.attach", session=stale, app=world.medops_app) is True
    assert session_needs_step_up(org, "exec.attach", session=fresh, app=world.medops_app) is False
    rule(world, "ORG", None, action="exec.attach", enabled=False)
    assert session_needs_step_up(org, "exec.attach", session=stale, app=world.medops_app) is False


def test_approval_composes_strictest_and_never_attaches_to_secret_changes(world):
    rule(world, "ORG", None, action="app.deregister", require_approval=True)
    rule(
        world, "APP", world.medops_app.pk, action="app.deregister", require_approval=True, minimum_approvals=2
    )
    rule(world, "ORG", None, action="secret.change", require_approval=True)
    on_app = effective(world.org.pk, "app.deregister", app_id=world.medops_app.pk)
    elsewhere = effective(world.org.pk, "app.deregister", app_id=world.platform_app.pk)
    assert (on_app.approval, on_app.minimum_approvals) == (True, 2)
    assert (elsewhere.approval, elsewhere.minimum_approvals) == (True, 1)
    assert effective(world.org.pk, "secret.change").approval is False
    assert effective(world.org.pk, "cluster.decommission").approval is False


SOFT_DELETE = "mutation($i:SoftDeleteAppInput!){softDeleteApp(input:$i){ok errors{code approvalRequestId}}}"
DECIDE = (
    "mutation($i:DecideActionApprovalInput!){%s(input:$i){ok errors{code message} data{status approvals}}}"
)


def _admin(world, label):
    user = make_user(label + uuid4().hex[:6])
    Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        user,
        permissions=[Permission.ORG_READ, Permission.ORG_UPDATE, Permission.APP_DELETE, Permission.APP_READ],
        kind="ORG",
        scope_id=world.org.pk,
        slug=uuid4().hex,
    )
    _token, headers = http_token(world, scopes=("admin",), actor=user)
    return user, headers


@pytest.mark.django_db(transaction=True)
def test_a_delete_with_approval_runs_once_for_the_requester_after_a_teammate_approves(client):
    from astrolift_identity.models import ActionApprovalRequest
    from astrolift_registry.models import RegisteredApp

    world = ScopeWorld("approval-" + uuid4().hex[:8])
    alice, alice_h = _admin(world, "alice")
    bob, bob_h = _admin(world, "bob")
    ActionSafetyRule.objects.create(
        organization=world.org,
        scope_kind="ORG",
        scope_id=None,
        action="app.deregister",
        require_approval=True,
        allow_self_approval=False,
    )
    app_id = str(world.medops_app.guid)

    def delete(headers):
        return graphql_http(client, headers, SOFT_DELETE, {"i": {"id": app_id}})["data"]["softDeleteApp"]

    def decide(headers, field, request_id):
        return graphql_http(client, headers, DECIDE % field, {"i": {"id": request_id}})["data"][field]

    first, again = delete(alice_h), delete(alice_h)
    assert first["ok"] is False and first["errors"][0]["code"] == "APPROVAL_REQUIRED"
    request_id = first["errors"][0]["approvalRequestId"]
    assert again["errors"][0]["approvalRequestId"] == request_id
    assert RegisteredApp.objects.get(pk=world.medops_app.pk).deleted_at is None

    own = decide(alice_h, "approveActionRequest", request_id)
    assert own["ok"] is False and "another approver" in own["errors"][0]["message"]

    approved = decide(bob_h, "approveActionRequest", request_id)
    assert approved["ok"] is True and approved["data"] == {"status": "approved", "approvals": 1}

    # Bob's approval is not Bob's to spend: his own delete opens his own request.
    bobs = delete(bob_h)
    assert (
        bobs["errors"][0]["code"] == "APPROVAL_REQUIRED"
        and bobs["errors"][0]["approvalRequestId"] != request_id
    )
    assert RegisteredApp.objects.get(pk=world.medops_app.pk).deleted_at is None

    done = delete(alice_h)
    assert done == {"ok": True, "errors": []}
    assert RegisteredApp.all_objects.get(pk=world.medops_app.pk).deleted_at is not None
    row = ActionApprovalRequest.objects.get(guid=request_id)
    assert row.status == "consumed" and row.consumed_at is not None
    assert decide(bob_h, "approveActionRequest", request_id)["errors"][0]["code"] == "PRECONDITION"


@pytest.mark.django_db(transaction=True)
def test_self_approval_where_the_rule_allows_it_and_an_expired_request_is_never_spent(client):
    import datetime as dt

    from django.utils import timezone

    from astrolift_identity.models import ActionApprovalDecision, ActionApprovalRequest

    world = ScopeWorld("selfappr-" + uuid4().hex[:8])
    alice, alice_h = _admin(world, "alice")
    ActionSafetyRule.objects.create(
        organization=world.org,
        scope_kind="ORG",
        scope_id=None,
        action="app.deregister",
        require_approval=True,
    )
    variables = {"i": {"id": str(world.medops_app.guid)}}
    request_id = graphql_http(client, alice_h, SOFT_DELETE, variables)["data"]["softDeleteApp"]["errors"][0][
        "approvalRequestId"
    ]
    mine = graphql_http(client, alice_h, DECIDE % "approveActionRequest", {"i": {"id": request_id}})
    assert mine["data"]["approveActionRequest"]["data"]["status"] == "approved"
    assert ActionApprovalDecision.objects.get(request__guid=request_id).self_approval is True

    ActionApprovalRequest.objects.filter(guid=request_id).update(
        expires_at=timezone.now() - dt.timedelta(seconds=1)
    )
    retry = graphql_http(client, alice_h, SOFT_DELETE, variables)["data"]["softDeleteApp"]
    assert (
        retry["errors"][0]["code"] == "APPROVAL_REQUIRED"
        and retry["errors"][0]["approvalRequestId"] != request_id
    )
    assert ActionApprovalRequest.objects.get(guid=request_id).status == "expired"


@pytest.mark.django_db(transaction=True)
def test_rules_api_refuses_approval_on_secret_changes_and_points_at_the_app_setting(client):
    world = ScopeWorld("apprrule-" + uuid4().hex[:8])
    _alice, alice_h = _admin(world, "alice")
    reply = graphql_http(
        client,
        alice_h,
        "mutation($i:SetActionSafetyRuleInput!){setActionSafetyRule(input:$i){ok errors{field message}}}",
        {"i": {"action": "secret.change", "scopeKind": "ORG", "requireApproval": True}},
    )["data"]["setActionSafetyRule"]
    assert reply["ok"] is False and reply["errors"][0]["field"] == "requireApproval"
    assert "approval setting on the app" in reply["errors"][0]["message"]


@pytest.mark.django_db(transaction=True)
def test_a_rule_write_changes_only_the_switches_it_sends(client):
    world = ScopeWorld("partial-" + uuid4().hex[:8])
    _alice, alice_h = _admin(world, "alice")
    mutation = "mutation($i:SetActionSafetyRuleInput!){setActionSafetyRule(input:$i){ok}}"
    for patch in (
        {"enabled": False},
        {"requireApproval": True, "minimumApprovals": 2},
        {"allowSelfApproval": False},
    ):
        variables = {"i": {"action": "app.deregister", "scopeKind": "ORG", **patch}}
        assert graphql_http(client, alice_h, mutation, variables)["data"]["setActionSafetyRule"]["ok"] is True
    row = ActionSafetyRule.objects.get(organization=world.org, action="app.deregister")
    assert (row.enabled, row.require_approval, row.minimum_approvals, row.allow_self_approval) == (
        False,
        True,
        2,
        False,
    )


@pytest.mark.django_db(transaction=True)
def test_rules_list_names_each_scope_and_clearing_a_narrower_rule_inherits_again(client):
    world = ScopeWorld("rules-" + uuid4().hex[:8])
    _alice, alice_h = _admin(world, "alice")
    app_guid = str(world.medops_app.guid)
    setter = "mutation($i:SetActionSafetyRuleInput!){setActionSafetyRule(input:$i){ok}}"
    graphql_http(
        client, alice_h, setter, {"i": {"action": "secret.change", "scopeKind": "ORG", "enabled": False}}
    )
    graphql_http(
        client,
        alice_h,
        setter,
        {"i": {"action": "secret.change", "scopeKind": "APP", "scopeId": app_guid, "enabled": True}},
    )
    listed = graphql_http(
        client, alice_h, "{astroliftActionSafetyRules{action scopeKind scopeId scopeLabel enabled}}", {}
    )["data"]["astroliftActionSafetyRules"]
    by_kind = {row["scopeKind"]: row for row in listed}
    assert by_kind["APP"] == {
        "action": "secret.change",
        "scopeKind": "APP",
        "scopeId": app_guid,
        "scopeLabel": world.medops_app.name or world.medops_app.slug,
        "enabled": True,
    }
    assert by_kind["ORG"]["scopeId"] is None and by_kind["ORG"]["enabled"] is False

    cleared = graphql_http(
        client,
        alice_h,
        "mutation($i:ClearActionSafetyRuleInput!){clearActionSafetyRule(input:$i){ok}}",
        {"i": {"action": "secret.change", "scopeKind": "APP", "scopeId": app_guid}},
    )["data"]["clearActionSafetyRule"]
    assert cleared["ok"] is True
    assert effective(world.org.pk, "secret.change", app_id=world.medops_app.pk).enabled is False


@pytest.mark.django_db(transaction=True)
def test_a_person_rule_is_addressed_by_user_id_and_a_malformed_scope_is_not_found(client):
    world = ScopeWorld("person-" + uuid4().hex[:8])
    alice, alice_h = _admin(world, "alice")
    setter = "mutation($i:SetActionSafetyRuleInput!){setActionSafetyRule(input:$i){ok errors{code field}}}"

    def put(scope_id):
        variables = {
            "i": {
                "action": "app.deregister",
                "scopeKind": "USER",
                "scopeId": scope_id,
                "requireApproval": True,
            }
        }
        return graphql_http(client, alice_h, setter, variables)["data"]["setActionSafetyRule"]

    assert put(str(alice.pk))["ok"] is True
    assert effective(world.org.pk, "app.deregister", user_id=alice.pk).approval is True
    for bad in ("not-a-guid", "99999999", ""):
        assert put(bad)["errors"][0]["code"] in {"NOT_FOUND", "VALIDATION"}
    listed = graphql_http(client, alice_h, "{astroliftActionSafetyRules{scopeKind scopeId scopeLabel}}", {})
    assert {"scopeKind": "USER", "scopeId": str(alice.pk), "scopeLabel": alice.email} in listed["data"][
        "astroliftActionSafetyRules"
    ]


@pytest.mark.django_db(transaction=True)
def test_approvers_with_the_authority_are_notified_and_the_requester_hears_the_outcome(client):
    from astrolift_operations.models import Notification

    world = ScopeWorld("notify-" + uuid4().hex[:8])
    alice, alice_h = _admin(world, "alice")
    bob, bob_h = _admin(world, "bob")
    viewer = make_user("viewer" + uuid4().hex[:6])
    Member.objects.create(user=viewer, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(viewer, permissions=[Permission.ORG_READ], kind="ORG", scope_id=world.org.pk, slug=uuid4().hex)
    ActionSafetyRule.objects.create(
        organization=world.org,
        scope_kind="ORG",
        scope_id=None,
        action="app.deregister",
        require_approval=True,
    )
    variables = {"i": {"id": str(world.medops_app.guid)}}
    request_id = graphql_http(client, alice_h, SOFT_DELETE, variables)["data"]["softDeleteApp"]["errors"][0][
        "approvalRequestId"
    ]
    graphql_http(client, alice_h, SOFT_DELETE, variables)  # asking again notifies nobody twice

    def inbox(user):
        return list(
            Notification.objects.filter(user=user, kind="action_approval").values_list("title", "link")
        )

    assert inbox(bob) == [
        (
            f"Approval requested: Deregister an app {world.medops_app.name}",
            "/administration/permissions/safety",
        )
    ]
    assert inbox(viewer) == [] and inbox(alice) == []

    graphql_http(client, bob_h, DECIDE % "approveActionRequest", {"i": {"id": request_id}})
    assert inbox(alice) == [
        (f"Approved: Deregister an app {world.medops_app.name}", "/administration/permissions/safety")
    ]
