"""Tests for AstroliftSession sidecar + revoke/heartbeat mutations (#480 / #495 / #498).

Covers:

* Middleware ``record_session`` creates the sidecar row, throttles
  ``last_seen_at`` writes, backfills ``client_kind=web`` for legacy
  sessions, enforces per-kind quotas (#495).
* ``revoke_astrolift_session`` mutation: owner path, org-admin path,
  deny path, current-session protection, idempotent re-revoke (#480).
* ``heartbeat_session`` mutation: bypasses the throttle, updates
  ``last_seen_at``, requires authentication (#498).
* ``astrolift_active_sessions`` query: surfaces ``client_kind`` +
  ``last_seen_at`` + per-row GUID id.
* ``_prune_stale_sessions_sync``: respects per-kind TTL, soft-revokes
  rows past the threshold, drops the underlying django_session
  cookie state.
* Per-client quota: evicts oldest live session when a new one would
  exceed the cap; audit emits ``session.quota_evicted``.

All tests run against real Postgres (the django_db marker covers the
sidecar + django_session rows together). Activity body is invoked
synchronously rather than through a Temporal env — per the workspace
memory on activity-level testing being more reliable than
transaction=True workflow tests.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session as DjangoSession
from django.test import RequestFactory
from django.utils import timezone

from astrolift_identity.models import (
    DEFAULT_MAX_SESSIONS_PER_CLIENT_KIND,
    AstroliftSession,
    ClientKind,
    Member,
    Organization,
    RevocationReason,
)
from astrolift_identity.schema.mutations import (
    IdentityMutation,
    RevokeAstroliftSessionInput,
)
from astrolift_identity.sessions import (
    LAST_SEEN_WRITE_THROTTLE_SECONDS,
    SESSION_CLIENT_KIND_KEY,
    SessionTrackingMiddleware,
    record_session,
    revoke_session,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Skip OpenSearch side-effects when User / Member creates fire
    signal handlers — the test container doesn't have opensearch
    reachable (same pattern as ``test_members_query._no_opensearch``)."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


# ---- test helpers ---------------------------------------------------


def _user(email: str | None = None):
    if email is None:
        email = f"u-{uuid.uuid4().hex[:8]}@astrolift.dev"
    return User.objects.create(email=email, username=email.split("@")[0])


def _request_with_session(user, *, session_key: str | None = None, client_kind: str = ClientKind.WEB.value):
    """Build a RequestFactory request with a fully-populated session.

    Persists a django_session row so ``record_session`` has something
    to attach the sidecar to.
    """
    if session_key is None:
        session_key = uuid.uuid4().hex
    # Persist the django_session row directly — the test doesn't go
    # through the auth view that would normally create it.
    DjangoSession.objects.create(
        session_key=session_key,
        session_data="",  # empty encoded blob is fine for the sidecar
        expire_date=timezone.now() + dt.timedelta(days=7),
    )
    request = RequestFactory().get("/")
    request.user = user
    request.session = SimpleNamespace(
        session_key=session_key,
        get=lambda k, default=None: {SESSION_CLIENT_KIND_KEY: client_kind}.get(k, default),
        get_expiry_date=lambda: timezone.now() + dt.timedelta(days=7),
    )
    return request


def _info(user, request=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request or RequestFactory().get("/")))


def _ctx(org=None, user=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id if org else None,
            actor_user_id=user.id if user else None,
        )
    )


# ---- record_session middleware path --------------------------------


def test_record_session_creates_sidecar_row():
    user = _user()
    request = _request_with_session(user, client_kind=ClientKind.CLI.value)
    row = record_session(request)
    assert row is not None
    assert row.user_id == user.pk
    assert row.client_kind == ClientKind.CLI.value
    assert row.session_key == request.session.session_key
    assert row.last_seen_at is not None


def test_record_session_defaults_to_web_for_missing_client_kind():
    user = _user()
    # Build a session whose data dict has no SESSION_CLIENT_KIND_KEY.
    request = RequestFactory().get("/")
    request.user = user
    session_key = uuid.uuid4().hex
    DjangoSession.objects.create(
        session_key=session_key,
        session_data="",
        expire_date=timezone.now() + dt.timedelta(days=7),
    )
    request.session = SimpleNamespace(
        session_key=session_key,
        get=lambda k, default=None: default,  # never returns a client_kind
        get_expiry_date=lambda: timezone.now() + dt.timedelta(days=7),
    )
    row = record_session(request)
    assert row.client_kind == ClientKind.WEB.value


def test_record_session_idempotent_for_same_key():
    user = _user()
    request = _request_with_session(user)
    first = record_session(request)
    second = record_session(request)
    assert first.pk == second.pk
    assert AstroliftSession.objects.filter(session_key=request.session.session_key).count() == 1


def test_record_session_throttles_last_seen_updates():
    """A chatty client (the FE polls every few seconds) must not
    write last_seen_at on every request — the throttle exists so we
    don't hammer the row."""
    user = _user()
    request = _request_with_session(user)
    first = record_session(request)
    initial = first.last_seen_at
    # Immediate re-touch: within the throttle window, no write.
    second = record_session(request)
    second.refresh_from_db()
    assert second.last_seen_at == initial

    # Backdate the row past the throttle, re-touch, expect a fresh
    # last_seen_at.
    past = timezone.now() - dt.timedelta(seconds=LAST_SEEN_WRITE_THROTTLE_SECONDS + 5)
    AstroliftSession.objects.filter(pk=first.pk).update(last_seen_at=past)
    third = record_session(request)
    third.refresh_from_db()
    assert third.last_seen_at > past


def test_record_session_returns_none_for_anonymous_request():
    from django.contrib.auth.models import AnonymousUser

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SimpleNamespace(
        session_key=None,
        get=lambda k, default=None: default,
        get_expiry_date=lambda: None,
    )
    assert record_session(request) is None


# ---- per-kind quota enforcement (#495) -----------------------------


def test_quota_enforcement_evicts_oldest_on_overflow(monkeypatch):
    """When a new session would push the user above the per-kind
    cap, the oldest live session of that kind is auto-revoked."""
    monkeypatch.setattr(
        "astrolift_identity.sessions._quota_for",
        lambda kind: 2,
    )
    user = _user()
    keys = []
    rows = []
    for _ in range(3):
        req = _request_with_session(user, client_kind=ClientKind.CLI.value)
        keys.append(req.session.session_key)
        rows.append(record_session(req))

    live = list(
        AstroliftSession.objects.filter(
            user=user,
            client_kind=ClientKind.CLI.value,
        ).order_by("created_at")
    )
    assert len(live) == 2, "quota=2 should leave exactly 2 live rows"
    # The oldest row's session_key was the first one created — should
    # now be soft-deleted in all_objects.
    evicted = AstroliftSession.all_objects.filter(session_key=keys[0]).first()
    assert evicted is not None
    assert evicted.revoked_at is not None
    assert evicted.revocation_reason == RevocationReason.QUOTA_EVICTION


def test_quota_isolated_per_client_kind(monkeypatch):
    """A web session quota does NOT count against the CLI quota for
    the same user (we model 'concurrent devices of a kind', not
    'concurrent everything')."""
    monkeypatch.setattr("astrolift_identity.sessions._quota_for", lambda kind: 2)
    user = _user()
    for _ in range(2):
        req = _request_with_session(user, client_kind=ClientKind.WEB.value)
        record_session(req)
    for _ in range(2):
        req = _request_with_session(user, client_kind=ClientKind.CLI.value)
        record_session(req)
    assert AstroliftSession.objects.filter(user=user, client_kind=ClientKind.WEB.value).count() == 2
    assert AstroliftSession.objects.filter(user=user, client_kind=ClientKind.CLI.value).count() == 2


def test_default_quota_includes_all_known_kinds():
    """Smoke: the in-code defaults cover every ClientKind value so a
    fresh install can't accidentally trip an unconfigured quota
    lookup."""
    for kind in ClientKind.values:
        assert kind in DEFAULT_MAX_SESSIONS_PER_CLIENT_KIND


# ---- revoke_astrolift_session mutation (#480) ---------------------


def test_revoke_own_session_succeeds():
    user = _user()
    # The session being revoked is a *different* session than the
    # caller's current request — that's what self-revoke means here.
    target_req = _request_with_session(user)
    target = record_session(target_req)

    caller_req = _request_with_session(user)
    record_session(caller_req)

    with _ctx(user=user):
        result = IdentityMutation().revoke_astrolift_session(
            _info(user, request=caller_req),
            input=RevokeAstroliftSessionInput(session_id=str(target.guid)),
        )
    assert result.ok, result.errors
    assert result.data.revoked is True

    target.refresh_from_db()
    assert target.revoked_at is not None
    assert target.revocation_reason == RevocationReason.USER_REVOKE
    # Underlying django_session is gone so the cookie fails auth.
    assert not DjangoSession.objects.filter(session_key=target_req.session.session_key).exists()


def test_revoke_other_users_session_denied_for_non_admin(permission_resolver):
    owner = _user()
    intruder = _user()
    target_req = _request_with_session(owner)
    target = record_session(target_req)

    intruder_req = _request_with_session(intruder)
    record_session(intruder_req)

    org = Organization.objects.create(name="X", slug="x")
    Member.objects.create(user=owner, scope_kind=Member.ScopeKind.ORG.value, scope_id=org.id)
    Member.objects.create(user=intruder, scope_kind=Member.ScopeKind.ORG.value, scope_id=org.id)
    # Intruder lacks ORG_MANAGE_MEMBERS — even though they share the
    # active org, they can't reach the admin-revoke branch.
    with _ctx(org, intruder):
        result = IdentityMutation().revoke_astrolift_session(
            _info(intruder, request=intruder_req),
            input=RevokeAstroliftSessionInput(session_id=str(target.guid)),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    target.refresh_from_db()
    assert target.revoked_at is None


def test_revoke_other_session_allowed_for_org_admin(permission_resolver):
    owner = _user()
    admin = _user()
    org = Organization.objects.create(name="X", slug="x")
    Member.objects.create(user=owner, scope_kind=Member.ScopeKind.ORG.value, scope_id=org.id)
    Member.objects.create(user=admin, scope_kind=Member.ScopeKind.ORG.value, scope_id=org.id)

    target_req = _request_with_session(owner)
    target = record_session(target_req)
    admin_req = _request_with_session(admin)
    record_session(admin_req)

    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    with _ctx(org, admin):
        result = IdentityMutation().revoke_astrolift_session(
            _info(admin, request=admin_req),
            input=RevokeAstroliftSessionInput(
                session_id=str(target.guid),
                reason="laptop lost",
            ),
        )
    assert result.ok, result.errors
    assert result.data.revoked is True
    target.refresh_from_db()
    assert target.revoked_at is not None
    # The free-form reason is appended after the canonical tag with
    # a ':' separator.
    assert target.revocation_reason.startswith(RevocationReason.ADMIN_REVOKE)
    assert "laptop lost" in target.revocation_reason


def test_revoke_current_session_returns_precondition_error():
    """Revoking your own current session via this mutation would
    race the response holding the cookie — that path is
    logoutAllSessions(keep_current=False) instead."""
    user = _user()
    req = _request_with_session(user)
    row = record_session(req)

    with _ctx(user=user):
        result = IdentityMutation().revoke_astrolift_session(
            _info(user, request=req),
            input=RevokeAstroliftSessionInput(session_id=str(row.guid)),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    row.refresh_from_db()
    assert row.revoked_at is None


def test_revoke_idempotent():
    user = _user()
    target_req = _request_with_session(user)
    target = record_session(target_req)
    revoke_session(row=target, actor_user_id=user.pk)
    target.refresh_from_db()
    first_revoked_at = target.revoked_at

    caller_req = _request_with_session(user)
    record_session(caller_req)
    with _ctx(user=user):
        result = IdentityMutation().revoke_astrolift_session(
            _info(user, request=caller_req),
            input=RevokeAstroliftSessionInput(session_id=str(target.guid)),
        )
    assert result.ok, result.errors
    # Second call sees the already-revoked state and reports it as a
    # no-op rather than failing.
    assert result.data.revoked is False
    target.refresh_from_db()
    assert target.revoked_at == first_revoked_at


def test_revoke_unknown_id_returns_not_found():
    user = _user()
    caller_req = _request_with_session(user)
    record_session(caller_req)
    with _ctx(user=user):
        result = IdentityMutation().revoke_astrolift_session(
            _info(user, request=caller_req),
            input=RevokeAstroliftSessionInput(session_id=str(uuid.uuid4())),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- heartbeat_session mutation (#498) ----------------------------


def test_heartbeat_updates_last_seen_immediately():
    """Bypasses the per-minute throttle — the client explicitly
    asked for a write."""
    user = _user()
    req = _request_with_session(user)
    row = record_session(req)
    initial = row.last_seen_at
    assert initial is not None
    # Wait an imperceptible amount and ping; even within the throttle
    # window, the explicit heartbeat must update last_seen.
    with _ctx(user=user):
        result = IdentityMutation().heartbeat_session(_info(user, request=req))
    assert result.ok, result.errors
    row.refresh_from_db()
    assert row.last_seen_at >= initial
    # The payload carries the new timestamp so a CLI can confirm the
    # write took without re-querying.
    assert result.data.last_seen_at == row.last_seen_at


def test_heartbeat_requires_authentication():
    from django.contrib.auth.models import AnonymousUser

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SimpleNamespace(
        session_key=None,
        get=lambda k, default=None: default,
        get_expiry_date=lambda: None,
    )
    with _ctx():
        result = IdentityMutation().heartbeat_session(_info(AnonymousUser(), request=request))
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- astrolift_active_sessions query ------------------------------


def test_active_sessions_surface_client_kind_and_last_seen():
    from astrolift_identity.schema.queries import IdentityQuery

    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.MOBILE.value)
    row = record_session(req)

    result = IdentityQuery().astrolift_active_sessions(_info(user, request=req))
    assert len(result) == 1
    surfaced = result[0]
    assert surfaced.id == str(row.guid)
    assert surfaced.client_kind == ClientKind.MOBILE.value
    assert surfaced.last_seen_at is not None
    assert surfaced.is_current is True


def test_active_sessions_excludes_other_users():
    from astrolift_identity.schema.queries import IdentityQuery

    me = _user()
    other = _user()
    record_session(_request_with_session(me))
    record_session(_request_with_session(other))

    listing = IdentityQuery().astrolift_active_sessions(_info(me, request=_request_with_session(me)))
    # The viewer's own request creates a third row for `me`; the
    # `other` user's row must not appear in this list.
    user_ids = {AstroliftSession.objects.get(guid=row.id).user_id for row in listing}
    assert user_ids == {me.pk}


# ---- stale-session prune activity (#498) --------------------------


def test_prune_stale_sessions_revokes_past_per_kind_ttl(monkeypatch):
    """The activity walks every ClientKind, reads its per-kind TTL
    (Constance or default), and soft-revokes rows whose
    last_seen_at is older than that threshold."""
    from astrolift_workflows.activities.scheduled import _prune_stale_sessions_sync

    user = _user()
    # Tighten the WEB TTL to 1s so the test row qualifies immediately.
    monkeypatch.setattr(
        "astrolift_identity.models.session.DEFAULT_STALE_SESSION_TTL_SECONDS",
        {ck.value: 1 for ck in ClientKind},
    )
    # The activity reads DEFAULTS by reference from the same module,
    # so importing the symbol it actually uses:
    monkeypatch.setattr(
        "astrolift_identity.models.DEFAULT_STALE_SESSION_TTL_SECONDS",
        {ck.value: 1 for ck in ClientKind},
    )

    fresh_req = _request_with_session(user, client_kind=ClientKind.WEB.value)
    fresh = record_session(fresh_req)
    stale_req = _request_with_session(user, client_kind=ClientKind.WEB.value)
    stale = record_session(stale_req)
    AstroliftSession.objects.filter(pk=stale.pk).update(
        last_seen_at=timezone.now() - dt.timedelta(days=365),
    )

    revoked = _prune_stale_sessions_sync()
    assert revoked >= 1
    stale.refresh_from_db()
    assert stale.revoked_at is not None
    assert stale.revocation_reason == RevocationReason.AUTO_STALE
    # The fresh row survives.
    fresh.refresh_from_db()
    assert fresh.revoked_at is None


def test_prune_stale_sessions_drops_underlying_django_session(monkeypatch):
    """Pruning must hard-delete the django_session row so the cookie
    fails auth on the next request — soft-deleting only the sidecar
    would leave a working credential behind."""
    from astrolift_workflows.activities.scheduled import _prune_stale_sessions_sync

    monkeypatch.setattr(
        "astrolift_identity.models.DEFAULT_STALE_SESSION_TTL_SECONDS",
        {ck.value: 1 for ck in ClientKind},
    )

    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.CLI.value)
    row = record_session(req)
    AstroliftSession.objects.filter(pk=row.pk).update(
        last_seen_at=timezone.now() - dt.timedelta(days=365),
    )
    assert DjangoSession.objects.filter(session_key=req.session.session_key).exists()

    _prune_stale_sessions_sync()
    assert not DjangoSession.objects.filter(session_key=req.session.session_key).exists()


def test_prune_handles_never_seen_rows(monkeypatch):
    """Rows that have last_seen_at=NULL but are older than the
    threshold by created_at should also be pruned — covers tokens
    that were issued and never used."""
    from astrolift_workflows.activities.scheduled import _prune_stale_sessions_sync

    monkeypatch.setattr(
        "astrolift_identity.models.DEFAULT_STALE_SESSION_TTL_SECONDS",
        {ck.value: 1 for ck in ClientKind},
    )
    user = _user()
    row = AstroliftSession.objects.create(
        user=user,
        session_key="never-used-" + uuid.uuid4().hex,
        client_kind=ClientKind.CLI.value,
        last_seen_at=None,
    )
    # Backdate created_at past the threshold (auto_now_add prevents
    # the field from being set directly at creation).
    AstroliftSession.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - dt.timedelta(days=365),
    )
    revoked = _prune_stale_sessions_sync()
    assert revoked >= 1
    row.refresh_from_db()
    assert row.revoked_at is not None


# ---- middleware integration --------------------------------------


def test_middleware_records_session_after_view_runs():
    """SessionTrackingMiddleware runs record_session in the
    response cycle so the session_key is populated even when the
    view created the session itself (login mutations)."""
    user = _user()
    session_key = uuid.uuid4().hex
    DjangoSession.objects.create(
        session_key=session_key,
        session_data="",
        expire_date=timezone.now() + dt.timedelta(days=7),
    )

    def view(request):
        from django.http import HttpResponse

        request.user = user
        request.session = SimpleNamespace(
            session_key=session_key,
            get=lambda k, default=None: default,
            get_expiry_date=lambda: timezone.now() + dt.timedelta(days=7),
        )
        return HttpResponse("ok")

    middleware = SessionTrackingMiddleware(view)
    request = RequestFactory().get("/")
    middleware(request)
    assert AstroliftSession.objects.filter(session_key=session_key, user=user).exists()


def test_middleware_never_breaks_request_on_internal_error():
    """The audit subsystem can raise during sidecar writes; the
    middleware must always return the wrapped response — never
    convert a 200 into a 500 because tracking failed."""

    def view(request):
        from django.http import HttpResponse

        return HttpResponse("ok", status=200)

    middleware = SessionTrackingMiddleware(view)
    request = RequestFactory().get("/")
    with patch(
        "astrolift_identity.sessions.record_session",
        side_effect=RuntimeError("boom"),
    ):
        response = middleware(request)
    assert response.status_code == 200
