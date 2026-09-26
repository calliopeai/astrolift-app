"""GraphQL subscriptions ignored the mobile bearer in connectionParams (#1943).

The mobile app has no sessionid cookie to send on a WS handshake — it opens
``/app/gql/config/ws/`` with graphql-ws and carries its ``alft_`` token in
``connectionParams`` instead (astrolift-mobile's Apollo client factory sends
``{"authorization": "Bearer <jwt>"}``). ``CookieAwareGraphQLWs.get_context``
only ever looked at the ``sessionid`` cookie, so every mobile subscription
ran as ``AnonymousUser`` with no tenant.

The fix resolves a ``connectionParams`` bearer through
``_resolve_user_and_tenant_from_bearer`` — the exact function
``ApiTokenAuthMiddleware`` and the exec/VNC relays use — via Strawberry's
``on_ws_connect`` hook, the earliest point a WS handshake can see
``connectionParams`` at all (``get_context`` runs before ``connection_init``
arrives). That carries every HTTP-bearer rule: revocation, and the
#1910/#1925 active-org-membership check inside ``verify_token``.

Two layers of tests:

* ``Test*`` classes below drive ``get_context`` + ``on_ws_connect`` directly
  against real Postgres rows (mirrors the style of
  ``test_tenant_membership_1925.py`` / ``test_token_membership_1910.py`` for
  the same WS auth chain) — the primary proof of the auth rules.
* The two ``test_real_*`` functions at the bottom drive the actual
  graphql-transport-ws protocol through Strawberry's own library code (no
  Django DB involved) to prove ``connectionParams`` genuinely reaches
  ``on_ws_connect`` the way the tests above assume, rather than trusting
  that assumption unverified.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
import strawberry
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client
from starlette.testclient import TestClient
from strawberry.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL

from astrolift_identity.api_tokens import DEFAULT_SCOPES, mint_token
from astrolift_identity.models import ApiToken, Member, Organization
from core.schema.ws_auth import _bearer_from_connection_params
from core.schema.ws_views import CookieAwareGraphQLWs

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---- pure unit tests: _bearer_from_connection_params ------------------


class TestBearerFromConnectionParams:
    def test_extracts_bearer_from_authorization_key(self):
        assert _bearer_from_connection_params({"authorization": "Bearer alft_at_x"}) == "alft_at_x"

    def test_key_match_is_case_insensitive(self):
        assert _bearer_from_connection_params({"Authorization": "Bearer alft_at_x"}) == "alft_at_x"

    def test_value_prefix_match_is_case_insensitive(self):
        assert _bearer_from_connection_params({"authorization": "bearer alft_at_x"}) == "alft_at_x"

    def test_requires_bearer_prefix(self):
        assert _bearer_from_connection_params({"authorization": "alft_at_x"}) == ""

    @pytest.mark.parametrize("payload", [None, [], "Bearer x", 5, {}])
    def test_rejects_non_dict_or_empty_payload(self, payload):
        assert _bearer_from_connection_params(payload) == ""

    def test_ignores_a_payload_without_an_authorization_key(self):
        # The mobile client sends ``authorization``, not ``authToken`` or
        # anything else — an unrelated key must not be mistaken for one.
        assert _bearer_from_connection_params({"authToken": "Bearer alft_at_x"}) == ""


# ---- get_context + on_ws_connect, against real Postgres rows ----------


def _org(tag: str) -> Organization:
    return Organization.objects.create(name=tag, slug=f"{tag}-1943-{uuid.uuid4().hex[:6]}")


def _user(tag: str) -> User:
    return User.objects.create_user(username=f"{tag}-1943-{uuid.uuid4().hex[:6]}")


def _member(org: Organization, user: User, *, active: bool = True, deleted: bool = False) -> User:
    row = Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.pk,
        is_active=active,
        lifecycle=Member.Lifecycle.ACTIVE if active else Member.Lifecycle.DEACTIVATED,
    )
    if deleted:
        row.soft_delete()
    return user


def _token(user: User, org: Organization, *, scopes=DEFAULT_SCOPES) -> str:
    minted = mint_token()
    ApiToken.objects.create(
        user=user,
        organization=org,
        name="1943",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=list(scopes),
    )
    return minted.plaintext


def _session_cookie(user: User) -> str:
    """A real, persisted sessionid for ``user`` — same mechanism the
    browser flow already relies on."""
    client = Client()
    client.force_login(user)
    return client.cookies["sessionid"].value


def _resolve(*, cookie: str = "", connection_params: dict | None = None):
    """Drive the real ``get_context`` + ``on_ws_connect`` pair, the way
    Strawberry does: get_context first (cookie/session resolution), then
    on_ws_connect once ``connection_init`` "arrives" with its payload."""
    handler = CookieAwareGraphQLWs(schema=None)
    ws = SimpleNamespace(
        headers={"cookie": cookie} if cookie else {},
        url=SimpleNamespace(path="/app/gql/config/ws/", hostname="astro.example.com"),
    )
    context = async_to_sync(handler.get_context)(ws, ws)
    context.connection_params = {} if connection_params is None else connection_params
    async_to_sync(handler.on_ws_connect)(context)
    return context


class TestConnectionParamsBearer:
    def test_valid_bearer_authenticates_and_pins_its_own_org(self):
        org = _org("acme")
        user = _member(org, _user("dana"))
        token = _token(user, org)

        ctx = _resolve(connection_params={"authorization": f"Bearer {token}"})

        assert ctx.request.user == user
        assert ctx.user == user
        assert ctx._ws_tenant.organization_id == org.pk

    def test_revoked_bearer_is_refused(self):
        org = _org("acme")
        user = _member(org, _user("dana"))
        token = _token(user, org)
        ApiToken.objects.filter(user=user, organization=org).update(is_revoked=True)

        ctx = _resolve(connection_params={"authorization": f"Bearer {token}"})

        assert ctx.request.user.is_authenticated is False
        assert ctx._ws_tenant is None

    def test_unknown_bearer_is_refused(self):
        ctx = _resolve(connection_params={"authorization": "Bearer alft_at_does_not_exist"})

        assert ctx.request.user.is_authenticated is False
        assert ctx._ws_tenant is None

    @pytest.mark.parametrize("ending", ["deactivated", "removed"])
    def test_bearer_whose_owner_left_the_org_is_refused(self, ending):
        # The #1910/#1925 rule: a credential is only as good as the
        # membership it was issued under. SCIM deprovisioning deactivates
        # the Member row but leaves the ApiToken and the account alone, so
        # this is what a deprovisioned mobile session looks like.
        org = _org("acme")
        user = _user("dana")
        token = _token(user, org)
        Member.objects.filter(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.pk).delete()
        _member(org, user, active=ending != "deactivated", deleted=ending == "removed")

        ctx = _resolve(connection_params={"authorization": f"Bearer {token}"})

        assert ctx.request.user.is_authenticated is False
        assert ctx._ws_tenant is None

    def test_a_members_other_token_in_a_different_org_keeps_working(self):
        # The membership check is per-(user, org), not a blanket account
        # freeze — pins that a token for an org the user is still active in
        # is unaffected by them having left a *different* org.
        acme, globex = _org("acme"), _org("globex")
        user = _member(acme, _user("dana"))
        _member(globex, user)
        acme_token = _token(user, acme)
        Member.objects.filter(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=globex.pk).update(
            is_active=False
        )

        ctx = _resolve(connection_params={"authorization": f"Bearer {acme_token}"})

        assert ctx.request.user == user
        assert ctx._ws_tenant.organization_id == acme.pk

    def test_no_bearer_leaves_the_cookie_flow_untouched(self):
        org = _org("acme")
        user = _member(org, _user("dana"))
        cookie = f"sessionid={_session_cookie(user)}"

        ctx = _resolve(cookie=cookie, connection_params={})

        assert ctx.request.user == user
        assert ctx._ws_tenant.organization_id == org.pk

    def test_malformed_connection_params_falls_back_to_the_cookie(self):
        org = _org("acme")
        user = _member(org, _user("dana"))
        cookie = f"sessionid={_session_cookie(user)}"

        ctx = _resolve(cookie=cookie, connection_params={"unrelated": "value"})

        assert ctx.request.user == user
        assert ctx._ws_tenant.organization_id == org.pk

    def test_a_bad_bearer_overrides_a_concurrent_cookie_session(self):
        # A presented bearer is the only credential tried, matching the
        # exec/VNC relays: it must not quietly fall back to a coincidental
        # cookie session the HTTP bearer path would itself have refused.
        cookie_org = _org("acme")
        cookie_user = _member(cookie_org, _user("cookie-dana"))
        cookie = f"sessionid={_session_cookie(cookie_user)}"

        bad_org = _org("globex")
        bad_token_user = _user("globex-eli")
        bad_token = _token(bad_token_user, bad_org)  # eli is never made a member of globex

        ctx = _resolve(cookie=cookie, connection_params={"authorization": f"Bearer {bad_token}"})

        assert ctx.request.user.is_authenticated is False
        assert ctx._ws_tenant is None

    def test_a_good_bearer_overrides_a_different_concurrent_cookie_session(self):
        cookie_org = _org("acme")
        cookie_user = _member(cookie_org, _user("cookie-dana"))
        cookie = f"sessionid={_session_cookie(cookie_user)}"

        bearer_org = _org("globex")
        bearer_user = _member(bearer_org, _user("globex-eli"))
        bearer_token = _token(bearer_user, bearer_org)

        ctx = _resolve(cookie=cookie, connection_params={"authorization": f"Bearer {bearer_token}"})

        assert ctx.request.user == bearer_user
        assert ctx._ws_tenant.organization_id == bearer_org.pk


# ---- real graphql-transport-ws wiring, no DB ---------------------------
#
# These prove Strawberry's own connection_init handling really does stash
# connectionParams onto the context and call on_ws_connect with it — the
# mechanism ``TestConnectionParamsBearer`` above assumes when it sets
# ``context.connection_params`` by hand instead of going through the wire
# protocol. A trivial schema keeps this DB-free: the fake resolver below
# never has to touch the database, and the cookie path never runs
# (session lookups short-circuit before hitting the DB when there is no
# sessionid cookie at all).


def _echo_schema_handler() -> CookieAwareGraphQLWs:
    @strawberry.type
    class Query:
        ok: bool = True

    @strawberry.type
    class Subscription:
        @strawberry.subscription
        async def tick(self, info: strawberry.types.Info) -> AsyncGenerator[str, None]:
            return
            yield  # pragma: no cover - never reached; keeps this an async generator

    schema = strawberry.Schema(query=Query, subscription=Subscription)
    return CookieAwareGraphQLWs(schema, subscription_protocols=[GRAPHQL_TRANSPORT_WS_PROTOCOL])


def test_real_handshake_delivers_connection_params_to_on_ws_connect(monkeypatch):
    seen: list[tuple[str, str]] = []

    async def _fake_resolver(token):
        seen.append((token, ""))
        return AnonymousUser(), None, None

    monkeypatch.setattr("core.schema.ws_views._resolve_bearer_identity_async", _fake_resolver)

    client = TestClient(_echo_schema_handler())
    with client.websocket_connect("/", subprotocols=[GRAPHQL_TRANSPORT_WS_PROTOCOL]) as ws:
        ws.send_json({"type": "connection_init", "payload": {"authorization": "Bearer alft_at_plumbing"}})
        assert ws.receive_json() == {"type": "connection_ack"}

    assert seen == [("alft_at_plumbing", "")]


def test_real_handshake_with_no_bearer_never_calls_bearer_resolution(monkeypatch):
    seen: list[object] = []

    async def _fake_resolver(token):
        seen.append(token)
        return AnonymousUser(), None, None

    monkeypatch.setattr("core.schema.ws_views._resolve_bearer_identity_async", _fake_resolver)

    client = TestClient(_echo_schema_handler())
    with client.websocket_connect("/", subprotocols=[GRAPHQL_TRANSPORT_WS_PROTOCOL]) as ws:
        ws.send_json({"type": "connection_init", "payload": {}})
        assert ws.receive_json() == {"type": "connection_ack"}

    assert seen == []


class TestBearerScopeCeiling:
    """A scoped-down token stays scoped down over WS (#1943): HTTP caps a
    bearer's grants at its scopes through the ``current_api_token``
    contextvar, and a subscription pins the same token before resolving."""

    def test_the_token_row_travels_with_the_context_and_is_pinned(self):
        from astrolift_identity.api_tokens import (
            SCOPE_READ_APPS,
            get_current_api_token,
            reset_current_api_token,
            set_current_api_token,
            token_scope_allows_permission,
        )
        from core.schema.ws_auth import pin_ws_identity

        org = _org("acme")
        user = _member(org, _user("dana"))
        context = _resolve(
            connection_params={"authorization": f"Bearer {_token(user, org, scopes=[SCOPE_READ_APPS])}"}
        )

        assert context._ws_api_token is not None
        from core.tenancy import tenant_context

        guard = set_current_api_token(None)
        try:
            with tenant_context(None):  # pin_ws_identity sets the tenant too; restore it
                pin_ws_identity(context)
            pinned = get_current_api_token()
            assert pinned is not None and pinned.pk == context._ws_api_token.pk
            assert token_scope_allows_permission(pinned, "app.read")
            assert not token_scope_allows_permission(pinned, "app.delete")
        finally:
            reset_current_api_token(guard)

    def test_a_cookie_session_pins_no_token(self):
        from core.schema.ws_auth import pin_ws_identity

        org = _org("acme")
        user = _member(org, _user("dana"))
        context = _resolve(cookie=f"sessionid={_session_cookie(user)}")
        assert getattr(context, "_ws_api_token", None) is None
        from core.tenancy import tenant_context

        with tenant_context(None):
            pin_ws_identity(context)  # no token to pin; must not raise
