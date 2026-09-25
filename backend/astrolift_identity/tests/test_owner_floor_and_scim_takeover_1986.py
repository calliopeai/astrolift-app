# ruff: noqa: F811  (pytest fixtures imported from test_anonymize_tenancy_1979)
"""Review round 2 of #1986: the last-owner floor, SCIM email takeover, oracles.

* Two revokes of an org's only two owners, run at once, could each see the
  other owner and leave none: the floor is now counted under the org row lock.
* An owner without an active ORG membership cannot act in the org, so they no
  longer count toward the floor.
* Anonymizing keeps the same floor, self-erasure included.
* A SCIM token could attach a pre-existing account (no other org) by userName
  and then PUT a new email; login matches by email, so the IdP could sign in
  as that person. Only accounts this org's SCIM created may be rewritten.
* Anonymize refused an outsider and an unknown user pk differently.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_identity.models import Member, RoleBinding
from astrolift_identity.schema.mutations import role_bindings as rb
from astrolift_identity.schema.mutations.role_bindings import RoleBindingMutations
from astrolift_identity.schema.mutations.types import RevokeRoleBindingInput
from astrolift_identity.tests.test_anonymize_tenancy_1979 import (  # noqa: F401 (fixtures)
    _anonymize,
    _bind,
    _denied,
    _erased,
    _member,
    _member_manager,
    _no_external_side_effects,
    _org,
    _untouched,
    _user,
    audit_capture,
    stock,
)
from astrolift_identity.tests.test_scim_provisioning import (
    USERS_PATH,
    _auth,
    _issue_token,
    _payload,
    _post,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


def _revoke(org, actor, binding):
    from types import SimpleNamespace

    info = SimpleNamespace(context=SimpleNamespace(user=actor, request=SimpleNamespace(user=actor)))
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        return RoleBindingMutations().revoke_role_binding(
            info, input=RevokeRoleBindingInput(id=str(binding.guid))
        )


# ---- last-owner floor ------------------------------------------------------


def test_the_floor_is_counted_under_the_org_lock(stock, monkeypatch):
    org = _org("a")
    owner_1 = _member(org, _user("o1"))
    owner_2 = _member(org, _user("o2"))
    b1 = _bind(owner_1, stock["org_owner"], "ORG", org.id)
    _bind(owner_2, stock["org_owner"], "ORG", org.id)

    from django.db import connection

    order: list[str] = []
    real_lock, real_count = rb._lock_org, rb._removes_last_owner

    def lock(org_id):
        assert connection.in_atomic_block
        order.append("lock")
        real_lock(org_id)

    def count(binding):
        order.append("count")
        return real_count(binding)

    monkeypatch.setattr(rb, "_lock_org", lock)
    monkeypatch.setattr(rb, "_removes_last_owner", count)

    result = _revoke(org, owner_2, b1)

    assert result.ok, result
    assert order[:2] == ["lock", "count"]


def test_an_owner_without_an_active_membership_does_not_keep_the_org_owned(stock):
    org = _org("a")
    acting = _member(org, _user("acting"))
    idle = _member(org, _user("idle"), active=False)
    acting_binding = _bind(acting, stock["org_owner"], "ORG", org.id)
    _bind(idle, stock["org_owner"], "ORG", org.id)

    result = _revoke(org, acting, acting_binding)

    assert _denied(result), result
    assert "last owner" in result.errors[0].message
    assert RoleBinding.objects.filter(pk=acting_binding.pk, deleted_at__isnull=True).exists()


def test_anonymizing_the_last_owner_is_refused_including_self(stock):
    org = _org("a")
    owner = _member(org, _user("owner"))
    _bind(owner, stock["org_owner"], "ORG", org.id)

    result = _anonymize(org, owner, owner)

    assert _denied(result), result
    assert _untouched(owner)


def test_anonymizing_one_of_two_owners_still_works(stock):
    org = _org("a")
    owner = _member(org, _user("owner"))
    other = _member(org, _user("other"))
    _bind(owner, stock["org_owner"], "ORG", org.id)
    _bind(other, stock["org_owner"], "ORG", org.id)

    result = _anonymize(org, owner, owner)

    assert result.ok, result
    assert _erased(owner)


# ---- anonymize oracle and platform accounts --------------------------------


def test_an_outsider_and_an_unknown_pk_are_refused_the_same_way(stock, audit_capture):
    a, b = _org("a"), _org("b")
    actor = _member_manager(a)
    outsider = _member(b, _user("outsider"))

    class Missing:
        pk = 10**9

    results = [_anonymize(a, actor, outsider), _anonymize(a, actor, Missing)]

    assert all(_denied(r) for r in results), results
    assert results[0].errors[0].message == results[1].errors[0].message
    assert _untouched(outsider)


def test_a_staff_account_is_refused(stock):
    org = _org("a")
    actor = _member_manager(org)
    staff = _member(org, _user("staff", is_staff=True))

    assert _denied(_anonymize(org, actor, staff))
    assert _untouched(staff)


# ---- SCIM takeover -----------------------------------------------------------


def _put_email(client, member, token, email):
    return client.put(
        f"{USERS_PATH}/{member.guid}",
        data=json.dumps(_payload(member.user.username, emails=[{"value": email, "primary": True}])),
        content_type="application/scim+json",
        **_auth(token),
    )


def test_an_attached_pre_existing_account_cannot_have_its_email_rewritten():
    acme = _org("acme")
    victim = User.objects.create_user(username="victim", email="victim@else.test", password="a-real-password")
    token = _issue_token(acme)
    client = Client()

    attached = _post(client, USERS_PATH, _payload("victim"), token)
    assert attached.status_code == 201, attached.content
    member = Member.objects.get(user=victim, scope_id=acme.id)

    resp = _put_email(client, member, token, "attacker@acme.test")

    assert resp.status_code == 403, resp.content
    victim.refresh_from_db()
    assert victim.email == "victim@else.test"


def test_an_account_this_org_created_can_still_be_rewritten():
    acme = _org("acme")
    token = _issue_token(acme)
    client = Client()

    created = _post(client, USERS_PATH, _payload("dana@acme.test"), token)
    assert created.status_code == 201, created.content
    member = Member.objects.get(user__username="dana@acme.test", scope_id=acme.id)

    resp = _put_email(client, member, token, "dana.reyes@acme.test")

    assert resp.status_code == 200, resp.content
    member.user.refresh_from_db()
    assert member.user.email == "dana.reyes@acme.test"


def test_put_refuses_an_email_another_account_uses():
    acme = _org("acme")
    User.objects.create_user(username="taken", email="taken@else.test")
    token = _issue_token(acme)
    client = Client()
    _post(client, USERS_PATH, _payload("dana@acme.test"), token)
    member = Member.objects.get(user__username="dana@acme.test", scope_id=acme.id)

    resp = _put_email(client, member, token, "TAKEN@else.test")

    assert resp.status_code == 409, resp.content
    member.user.refresh_from_db()
    assert member.user.email == "dana@acme.test"


def test_post_refuses_a_staff_account_with_the_generic_message():
    acme = _org("acme")
    User.objects.create_user(username="ops", email="ops@else.test", is_staff=True)
    token = _issue_token(acme)

    resp = _post(Client(), USERS_PATH, _payload("ops"), token)

    assert resp.status_code == 409
    assert "already in use" in resp.json()["detail"]
