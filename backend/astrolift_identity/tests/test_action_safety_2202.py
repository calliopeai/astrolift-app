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
def _default_on(settings):
    settings.ASTROLIFT_ACTION_SAFETY_DEFAULT_ON = True


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
