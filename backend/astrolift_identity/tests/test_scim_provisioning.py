"""End-to-end tests for the SCIM 2.0 provisioning surface (#78, #91).

The policy layer in ``astrolift_identity.scim`` is unit-tested in
``test_scim.py``; these tests cover the wire it now sits behind —
``/api/scim/v2/Users`` — plus the credential that authenticates it.

Covers:

* ``manage.py issue_scim_token`` writes ``Organization.scim_token_hash``
  (nothing wrote it before) and flips ``scim_enabled``; ``--revoke``
  turns provisioning back off.
* The endpoint answers only to a matching ``alft_st_`` bearer on an
  org with SCIM enabled, and scopes every read and write to that org.
* List honours ``?filter=`` and RFC 7644 paging; a filter on an
  attribute the platform does not store is refused rather than
  answered with an empty page.
* POST provisions a User + ORG Member; a second POST for the same
  person conflicts.
* DELETE and ``active=false`` deactivate (never delete), cut live
  sessions when it was the person's last org, and leave a person who
  is still in another org alone.
"""

from __future__ import annotations

import hashlib
import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client

from astrolift_identity.models import (
    AstroliftSession,
    Member,
    Organization,
    RevocationReason,
)
from astrolift_identity.scim import SCHEMA_USER

pytestmark = pytest.mark.django_db
User = get_user_model()

USERS_PATH = "/api/scim/v2/Users"


# ---- helpers ---------------------------------------------------------


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=slug.title(), slug=slug)


def _issue_token(org: Organization) -> str:
    """Run the producer command and return the plaintext token."""
    out = StringIO()
    call_command("issue_scim_token", "--org", org.slug, stdout=out)
    for line in out.getvalue().splitlines():
        if line.strip().startswith("token:"):
            return line.split("token:", 1)[1].strip()
    raise AssertionError(f"command printed no token: {out.getvalue()!r}")


def _auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _member(org: Organization, username: str, email: str = "", **user_kwargs) -> Member:
    user = User.objects.create_user(
        username=username,
        email=email or f"{username}@acme.test",
        **user_kwargs,
    )
    return Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


def _payload(user_name: str, **overrides) -> dict:
    body = {
        "schemas": [SCHEMA_USER],
        "userName": user_name,
        "name": {"givenName": "Dana", "familyName": "Reyes"},
        "emails": [{"value": user_name, "primary": True}],
        "active": True,
    }
    body.update(overrides)
    return body


def _post(client: Client, path: str, body: dict, token: str):
    return client.post(
        path,
        data=json.dumps(body),
        content_type="application/scim+json",
        **_auth(token),
    )


def _patch(client: Client, path: str, body: dict, token: str):
    return client.patch(
        path,
        data=json.dumps(body),
        content_type="application/scim+json",
        **_auth(token),
    )


# ---- the credential --------------------------------------------------


def test_issue_command_stores_only_the_hash_and_enables_scim():
    org = _org("acme")
    assert org.scim_token_hash == ""
    assert org.scim_enabled is False

    token = _issue_token(org)
    org.refresh_from_db()

    assert token.startswith("alft_st_")
    assert org.scim_enabled is True
    assert org.scim_token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in org.scim_token_hash


def test_rotating_retires_the_previous_token():
    org = _org("acme")
    first = _issue_token(org)
    second = _issue_token(org)
    client = Client()

    assert first != second
    assert client.get(USERS_PATH, **_auth(first)).status_code == 401
    assert client.get(USERS_PATH, **_auth(second)).status_code == 200


def test_endpoint_refuses_missing_and_wrong_credentials():
    org = _org("acme")
    _issue_token(org)
    client = Client()

    assert client.get(USERS_PATH).status_code == 401
    assert client.get(USERS_PATH, **_auth("alft_st_nope")).status_code == 401
    # An API token is not a SCIM token, however valid it is elsewhere.
    assert client.get(USERS_PATH, **_auth("alft_at_something")).status_code == 401


def test_revoking_stops_provisioning():
    org = _org("acme")
    token = _issue_token(org)
    client = Client()
    assert client.get(USERS_PATH, **_auth(token)).status_code == 200

    call_command("issue_scim_token", "--org", org.slug, "--revoke", stdout=StringIO())
    org.refresh_from_db()

    assert org.scim_enabled is False
    assert client.get(USERS_PATH, **_auth(token)).status_code == 401


def test_disabling_scim_stops_provisioning_without_rotating():
    """The advertised ``scimEnabled`` flag is load-bearing, not decor."""
    org = _org("acme")
    token = _issue_token(org)
    client = Client()
    assert client.get(USERS_PATH, **_auth(token)).status_code == 200

    org.scim_enabled = False
    org.save(update_fields=["scim_enabled", "updated_at", "version"])

    assert client.get(USERS_PATH, **_auth(token)).status_code == 401


# ---- list ------------------------------------------------------------


def test_list_returns_only_the_credentialed_org():
    acme, other = _org("acme"), _org("globex")
    ours = _member(acme, "alice")
    _member(acme, "bob")
    theirs = _member(other, "carol")
    token = _issue_token(acme)
    _issue_token(other)

    body = Client().get(USERS_PATH, **_auth(token)).json()

    assert body["totalResults"] == 2
    ids = {r["id"] for r in body["Resources"]}
    assert str(ours.guid) in ids
    assert str(theirs.guid) not in ids


def test_list_filters_by_username():
    acme = _org("acme")
    _member(acme, "alice")
    bob = _member(acme, "bob")
    token = _issue_token(acme)

    body = (
        Client()
        .get(
            USERS_PATH,
            {"filter": 'userName eq "bob"'},
            **_auth(token),
        )
        .json()
    )

    assert body["totalResults"] == 1
    assert body["Resources"][0]["id"] == str(bob.guid)


def test_list_refuses_a_filter_on_an_attribute_we_do_not_store():
    """An empty page reads as 'no such user' and makes the IdP create a
    duplicate, so an externalId filter has to fail loudly."""
    acme = _org("acme")
    _member(acme, "alice")
    token = _issue_token(acme)

    resp = Client().get(
        USERS_PATH,
        {"filter": 'externalId eq "idp-1"'},
        **_auth(token),
    )

    assert resp.status_code == 400
    assert resp.json()["scimType"] == "invalidFilter"


def test_list_rejects_unparseable_filter_syntax():
    acme = _org("acme")
    token = _issue_token(acme)

    resp = Client().get(USERS_PATH, {"filter": "userName ~~ bob"}, **_auth(token))

    assert resp.status_code == 400
    assert resp.json()["scimType"] == "invalidFilter"


def test_list_pages_with_start_index_and_count():
    acme = _org("acme")
    first = _member(acme, "alice")
    second = _member(acme, "bob")
    _member(acme, "carol")
    token = _issue_token(acme)
    client = Client()

    page = client.get(USERS_PATH, {"startIndex": 2, "count": 1}, **_auth(token)).json()

    assert page["totalResults"] == 3
    assert page["startIndex"] == 2
    assert page["itemsPerPage"] == 1
    assert [r["id"] for r in page["Resources"]] == [str(second.guid)]

    head = client.get(USERS_PATH, {"count": 1}, **_auth(token)).json()
    assert [r["id"] for r in head["Resources"]] == [str(first.guid)]


# ---- provisioning ----------------------------------------------------


def test_post_provisions_a_user_and_an_org_membership():
    acme = _org("acme")
    token = _issue_token(acme)
    client = Client()

    resp = _post(client, USERS_PATH, _payload("dana@acme.test"), token)

    assert resp.status_code == 201
    created = resp.json()
    user = User.objects.get(username="dana@acme.test")
    assert user.email == "dana@acme.test"
    assert user.first_name == "Dana"
    # SCIM people authenticate through the IdP; no local password.
    assert not user.has_usable_password()

    member = Member.objects.get(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=acme.id)
    assert member.is_active is True
    assert created["id"] == str(member.guid)
    # The id the IdP keeps has to resolve on the next call.
    assert client.get(f"{USERS_PATH}/{member.guid}", **_auth(token)).status_code == 200


def test_post_conflicts_when_the_person_is_already_provisioned():
    acme = _org("acme")
    token = _issue_token(acme)
    client = Client()
    _post(client, USERS_PATH, _payload("dana@acme.test"), token)

    resp = _post(client, USERS_PATH, _payload("dana@acme.test"), token)

    assert resp.status_code == 409
    assert resp.json()["scimType"] == "uniqueness"
    assert Member.objects.filter(user__username="dana@acme.test").count() == 1


def test_post_rejects_a_payload_with_no_email():
    acme = _org("acme")
    token = _issue_token(acme)

    resp = _post(Client(), USERS_PATH, {"userName": "dana@acme.test"}, token)

    assert resp.status_code == 400
    assert not User.objects.filter(username="dana@acme.test").exists()


def test_reprovisioning_a_deprovisioned_person_reuses_the_membership():
    acme = _org("acme")
    token = _issue_token(acme)
    client = Client()
    created = _post(client, USERS_PATH, _payload("dana@acme.test"), token).json()
    client.delete(f"{USERS_PATH}/{created['id']}", **_auth(token))

    resp = _post(client, USERS_PATH, _payload("dana@acme.test"), token)

    assert resp.status_code == 201
    assert resp.json()["id"] == created["id"]
    member = Member.objects.get(guid=created["id"])
    assert member.is_active is True
    assert member.lifecycle == Member.Lifecycle.ACTIVE
    assert Member.objects.filter(user=member.user, scope_id=acme.id).count() == 1


# ---- deprovisioning --------------------------------------------------


def test_patch_active_false_deactivates_and_cuts_live_sessions():
    acme = _org("acme")
    member = _member(acme, "alice")
    session = AstroliftSession.objects.create(user=member.user, session_key="sk-alice")
    token = _issue_token(acme)

    resp = _patch(
        Client(),
        f"{USERS_PATH}/{member.guid}",
        {"Operations": [{"op": "replace", "path": "active", "value": False}]},
        token,
    )

    assert resp.status_code == 200
    assert resp.json()["active"] is False

    member.refresh_from_db()
    assert member.is_active is False
    assert member.lifecycle == Member.Lifecycle.DEACTIVATED
    # Soft state, never a delete: the id the IdP holds still resolves.
    assert Member.objects.filter(guid=member.guid).exists()

    member.user.refresh_from_db()
    assert member.user.is_active is False

    session.refresh_from_db()
    assert session.revoked_at is not None
    assert session.revocation_reason == RevocationReason.SCIM_DEPROVISION

    # The tenant middleware resolves an org from active ORG rows only,
    # so this is the assertion that access is actually gone.
    assert not Member.objects.filter(
        user=member.user,
        scope_kind=Member.ScopeKind.ORG,
        is_active=True,
    ).exists()


def test_deprovision_leaves_a_membership_in_another_org_alone():
    acme, other = _org("acme"), _org("globex")
    member = _member(acme, "alice")
    elsewhere = Member.objects.create(
        user=member.user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=other.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    session = AstroliftSession.objects.create(user=member.user, session_key="sk-alice")
    token = _issue_token(acme)

    _patch(
        Client(),
        f"{USERS_PATH}/{member.guid}",
        {"active": False},
        token,
    )

    member.refresh_from_db()
    assert member.is_active is False

    elsewhere.refresh_from_db()
    assert elsewhere.is_active is True
    member.user.refresh_from_db()
    assert member.user.is_active is True
    session.refresh_from_db()
    assert session.revoked_at is None


def test_delete_deprovisions():
    acme = _org("acme")
    member = _member(acme, "alice")
    token = _issue_token(acme)

    resp = Client().delete(f"{USERS_PATH}/{member.guid}", **_auth(token))

    assert resp.status_code == 204
    member.refresh_from_db()
    assert member.is_active is False


def test_patch_active_true_reactivates():
    acme = _org("acme")
    member = _member(acme, "alice", is_active=False)
    member.is_active = False
    member.lifecycle = Member.Lifecycle.DEACTIVATED
    member.save(update_fields=["is_active", "lifecycle", "updated_at", "version"])
    token = _issue_token(acme)

    resp = _patch(
        Client(),
        f"{USERS_PATH}/{member.guid}",
        {"Operations": [{"op": "replace", "path": "active", "value": True}]},
        token,
    )

    assert resp.status_code == 200
    member.refresh_from_db()
    assert member.is_active is True
    assert member.lifecycle == Member.Lifecycle.ACTIVE
    member.user.refresh_from_db()
    assert member.user.is_active is True


def test_put_replaces_the_stored_attributes():
    acme = _org("acme")
    member = _member(acme, "alice", email="alice@acme.test")
    token = _issue_token(acme)

    resp = Client().put(
        f"{USERS_PATH}/{member.guid}",
        data=json.dumps(
            _payload(
                "alice",
                emails=[{"value": "alice.smith@acme.test", "primary": True}],
                name={"givenName": "Alice", "familyName": "Smith"},
            )
        ),
        content_type="application/scim+json",
        **_auth(token),
    )

    assert resp.status_code == 200
    member.user.refresh_from_db()
    assert member.user.email == "alice.smith@acme.test"
    assert member.user.last_name == "Smith"
    # userName is the login identity; a rename does not strand it.
    assert member.user.username == "alice"


def test_unsupported_patch_is_refused():
    acme = _org("acme")
    member = _member(acme, "alice")
    token = _issue_token(acme)

    resp = _patch(
        Client(),
        f"{USERS_PATH}/{member.guid}",
        {"Operations": [{"op": "replace", "path": "displayName", "value": "Nope"}]},
        token,
    )

    assert resp.status_code == 400
    member.refresh_from_db()
    assert member.is_active is True


# ---- scoping ---------------------------------------------------------


def test_another_orgs_member_is_not_addressable():
    acme, other = _org("acme"), _org("globex")
    theirs = _member(other, "carol")
    token = _issue_token(acme)
    client = Client()

    read = client.get(f"{USERS_PATH}/{theirs.guid}", **_auth(token))
    deprovision = client.delete(f"{USERS_PATH}/{theirs.guid}", **_auth(token))

    assert read.status_code == 404
    assert deprovision.status_code == 404
    theirs.refresh_from_db()
    assert theirs.is_active is True


def test_a_malformed_id_is_a_404_not_a_crash():
    acme = _org("acme")
    token = _issue_token(acme)

    resp = Client().get(f"{USERS_PATH}/not-a-uuid", **_auth(token))

    assert resp.status_code == 404
