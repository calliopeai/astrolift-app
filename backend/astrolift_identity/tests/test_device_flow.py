"""End-to-end tests for the CLI / mobile device-flow surface (#475).

Covers:

* ``device_flow.create_session`` mints a unique session_id, sets
  ``state=pending``, captures client metadata.
* ``poll_complete`` returns pending → issued → expired in sequence;
  approval flips state; per-session rate limit returns slow_down;
  expired pending rows transition lazily.
* ``approve_session`` / ``deny_session`` reject already-terminal rows.
* ``refresh_credentials`` rotates the refresh secret + access token;
  presenting the previous refresh after rotation is rejected (replay).
* REST views return correct status codes for every wire path
  documented in ``astrolift-cli/internal/auth/auth.go``.
* Issued ``alft_at_`` access tokens authenticate via the existing
  :class:`ApiTokenAuthMiddleware`.
* The browser approval surface requires auth; approve/deny POSTs
  drive the state machine; the multi-org picker is rendered + the
  org choice is tenancy-checked.
"""

from __future__ import annotations

import datetime as dt
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.utils import timezone

from astrolift_identity import device_flow
from astrolift_identity.middleware import ApiTokenAuthMiddleware
from astrolift_identity.models import (
    ApiToken,
    DeviceFlowSession,
    Member,
    Organization,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---- helpers ---------------------------------------------------------


def _make_user(email: str | None = None, password: str = "pw"):
    if email is None:
        import uuid

        email = f"u-{uuid.uuid4().hex[:8]}@astrolift.dev"
    user = User.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password=password,
    )
    return user


def _make_member(user, org):
    return Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


# ---- device_flow.create_session -------------------------------------


def test_create_session_returns_pending_row_with_unique_id():
    row1, sid1 = device_flow.create_session(client_label="cli on laptop")
    row2, sid2 = device_flow.create_session(client_label="cli elsewhere")
    assert sid1 != sid2
    assert row1.state == DeviceFlowSession.STATE_PENDING
    assert row1.session_guid == sid1
    assert row1.client_label == "cli on laptop"
    assert row1.expires_at > timezone.now()


def test_create_session_normalizes_client_kind_and_label():
    row, _ = device_flow.create_session(client_label="", client_kind="MOBILE")
    assert row.client_kind == "mobile"
    # label falls back to the kind when empty
    assert row.client_label == "mobile"


def test_create_session_captures_ip_and_user_agent():
    row, _ = device_flow.create_session(
        user_agent="astro-cli/1.2.3",
        client_ip="203.0.113.5",
    )
    assert row.user_agent == "astro-cli/1.2.3"
    assert row.client_ip == "203.0.113.5"


# ---- approve / deny ---------------------------------------------------


def test_approve_session_requires_pending():
    user = _make_user()
    row, _ = device_flow.create_session()
    assert device_flow.approve_session(row, user=user) is None
    assert row.state == DeviceFlowSession.STATE_APPROVED
    # second approve is rejected
    assert device_flow.approve_session(row, user=user) == "already_terminal"


def test_deny_session_rejects_terminal():
    user = _make_user()
    row, _ = device_flow.create_session()
    device_flow.approve_session(row, user=user)
    assert device_flow.deny_session(row) == "already_terminal"


def test_mark_expired_if_needed_flips_only_pending():
    row, _ = device_flow.create_session()
    # Force expiry into the past
    row.expires_at = timezone.now() - dt.timedelta(seconds=1)
    row.save(update_fields=["expires_at", "updated_at", "version"])
    assert device_flow.mark_expired_if_needed(row) is True
    assert row.state == DeviceFlowSession.STATE_EXPIRED
    # idempotent
    assert device_flow.mark_expired_if_needed(row) is False


# ---- poll_complete state machine -------------------------------------


def test_poll_complete_pending_then_issued():
    user = _make_user()
    org = Organization.objects.create(name="X", slug="x")
    row, sid = device_flow.create_session(client_label="cli")

    r = device_flow.poll_complete(sid)
    assert r.status == "pending"

    # approve and re-poll (advance the clock past the slow_down floor)
    device_flow.approve_session(row, user=user, organization=org)
    later = timezone.now() + device_flow.MIN_POLL_INTERVAL + dt.timedelta(seconds=1)
    r2 = device_flow.poll_complete(sid, now=later)
    assert r2.status == "issued"
    assert r2.credentials is not None
    assert r2.credentials.access_token.startswith("alft_at_")
    assert r2.credentials.refresh_token.startswith("alft_rt_")
    # row is consumed; api_token row created
    row.refresh_from_db()
    assert row.state == DeviceFlowSession.STATE_CONSUMED
    assert row.api_token_id is not None
    assert row.api_token.scopes == device_flow.token_scopes_for_client_kind("cli")
    # second poll after consume returns expired (single-use)
    r3 = device_flow.poll_complete(sid, now=later + dt.timedelta(seconds=5))
    assert r3.status == "expired"


def test_poll_complete_returns_slow_down_on_tight_loop():
    row, sid = device_flow.create_session()
    first = device_flow.poll_complete(sid)
    assert first.status == "pending"
    # Immediate second poll inside the floor: slow_down
    second = device_flow.poll_complete(sid)
    assert second.status == "slow_down"


def test_poll_complete_unknown_session():
    assert device_flow.poll_complete("nope").status == "unknown"


def test_poll_complete_denied():
    row, sid = device_flow.create_session()
    device_flow.deny_session(row)
    r = device_flow.poll_complete(sid)
    assert r.status == "denied"


def test_poll_complete_expires_pending_lazily():
    row, sid = device_flow.create_session()
    row.expires_at = timezone.now() - dt.timedelta(seconds=1)
    row.save(update_fields=["expires_at", "updated_at", "version"])
    r = device_flow.poll_complete(sid)
    assert r.status == "expired"


# ---- refresh + replay detection --------------------------------------


def _approve_and_issue(now=None):
    user = _make_user()
    org = Organization.objects.create(name="X", slug="x")
    row, sid = device_flow.create_session(client_label="cli")
    device_flow.approve_session(row, user=user, organization=org)
    later = (now or timezone.now()) + device_flow.MIN_POLL_INTERVAL + dt.timedelta(seconds=1)
    r = device_flow.poll_complete(sid, now=later)
    assert r.status == "issued"
    return user, org, row, r.credentials, later


def test_refresh_rotates_access_and_refresh():
    _, _, row, creds, after = _approve_and_issue()
    # Capture the pre-rotation api_token id by refreshing the row
    # (the helper hands back the pre-consume instance — the consume
    # path runs in a transaction with select_for_update against a
    # fresh fetch).
    row.refresh_from_db()
    prior_api_token_id = row.api_token_id
    assert prior_api_token_id is not None

    later = after + dt.timedelta(seconds=5)
    r = device_flow.refresh_credentials(creds.refresh_token, now=later)
    assert r.status == "issued"
    assert r.credentials is not None
    assert r.credentials.access_token != creds.access_token
    assert r.credentials.refresh_token != creds.refresh_token

    # Row points at a new api_token row; the prior one is revoked.
    row.refresh_from_db()
    assert row.api_token_id is not None
    assert row.api_token_id != prior_api_token_id
    assert row.api_token.scopes == device_flow.token_scopes_for_client_kind("cli")
    prior = ApiToken.all_objects.get(pk=prior_api_token_id)
    assert prior.is_revoked is True


def test_refresh_replay_after_rotation_is_rejected():
    _, _, _, creds, after = _approve_and_issue()
    later = after + dt.timedelta(seconds=5)
    first = device_flow.refresh_credentials(creds.refresh_token, now=later)
    assert first.status == "issued"
    # Reuse the ORIGINAL refresh: must be rejected; the hash is gone
    second = device_flow.refresh_credentials(creds.refresh_token, now=later + dt.timedelta(seconds=1))
    assert second.status == "unknown"


def test_refresh_with_bad_prefix_is_unknown():
    assert device_flow.refresh_credentials("not_a_refresh_token").status == "unknown"
    assert device_flow.refresh_credentials("").status == "unknown"
    assert device_flow.refresh_credentials("alft_at_lookalike").status == "unknown"


def test_refresh_expired_chain_is_rejected_and_invalidates():
    _, _, row, creds, after = _approve_and_issue()
    # Force the refresh TTL to be already past
    row.refresh_from_db()
    row.refresh_token_expires_at = after - dt.timedelta(seconds=1)
    row.save(update_fields=["refresh_token_expires_at", "updated_at", "version"])

    r = device_flow.refresh_credentials(creds.refresh_token, now=after + dt.timedelta(seconds=1))
    assert r.status == "expired"
    row.refresh_from_db()
    assert row.refresh_token_hash == ""
    # api_token revoked
    assert ApiToken.objects.get(pk=row.api_token_id).is_revoked is True


# ---- issued access token authenticates middleware ---------------------


def test_issued_access_token_authenticates_middleware():
    _, _, _, creds, _ = _approve_and_issue()

    captured: dict = {}

    def _view(request):
        captured["request"] = request
        return HttpResponse(b"ok", status=200)

    mw = ApiTokenAuthMiddleware(_view)
    request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {creds.access_token}")
    request.user = AnonymousUser()
    response = mw(request)
    assert response.status_code == 200
    assert captured["request"].user.is_authenticated


# ---- REST views ------------------------------------------------------


def _post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def test_view_start_returns_session_envelope():
    client = Client()
    r = _post_json(client, "/api/cli/v1/auth/start", {})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "session_id",
        "login_url",
        "poll_interval_seconds",
        "expires_in_seconds",
    }
    assert body["session_id"]
    assert body["login_url"].endswith(f"/cli/auth/device/{body['session_id']}/")
    assert body["expires_in_seconds"] > 0
    assert body["poll_interval_seconds"] == device_flow.POLL_INTERVAL_SECONDS


def test_view_start_persists_client_metadata():
    client = Client()
    r = _post_json(client, "/api/cli/v1/auth/start", {"client_label": "lab cli", "client_kind": "mobile"})
    body = r.json()
    row = DeviceFlowSession.objects.get(session_guid=body["session_id"])
    assert row.client_label == "lab cli"
    assert row.client_kind == "mobile"


def test_view_complete_returns_202_while_pending():
    client = Client()
    start = _post_json(client, "/api/cli/v1/auth/start", {}).json()
    r = _post_json(client, "/api/cli/v1/auth/complete", {"session_id": start["session_id"]})
    assert r.status_code == 202
    # Body is empty per the wire contract
    assert r.content == b""


def test_view_complete_returns_404_on_unknown_session():
    client = Client()
    r = _post_json(client, "/api/cli/v1/auth/complete", {"session_id": "missing"})
    assert r.status_code == 404


def test_view_complete_requires_session_id():
    client = Client()
    r = _post_json(client, "/api/cli/v1/auth/complete", {})
    assert r.status_code == 400


def test_view_refresh_requires_token():
    client = Client()
    r = _post_json(client, "/api/cli/v1/auth/refresh", {})
    assert r.status_code == 400


def test_view_refresh_unknown_returns_401():
    client = Client()
    r = _post_json(client, "/api/cli/v1/auth/refresh", {"refresh_token": "alft_rt_doesnotexist"})
    assert r.status_code == 401


def test_view_complete_returns_200_after_approval():
    # Drive the full flow through the REST views.
    user = _make_user()
    org = Organization.objects.create(name="X", slug="x")
    _make_member(user, org)
    client = Client()
    start = _post_json(client, "/api/cli/v1/auth/start", {}).json()
    sid = start["session_id"]
    # Approve via the business layer (the browser approval is tested
    # separately) so this stays focused on the /complete path.
    row = DeviceFlowSession.objects.get(session_guid=sid)
    device_flow.approve_session(row, user=user, organization=org)

    # First poll happens immediately on /start in tests; advance the
    # MIN_POLL_INTERVAL by waiting it out via a synthetic now in the
    # business layer. The view itself doesn't take a now arg, so we
    # cheat by zeroing polled_at.
    row.refresh_from_db()
    row.polled_at = None
    row.save(update_fields=["polled_at", "updated_at", "version"])

    r = _post_json(client, "/api/cli/v1/auth/complete", {"session_id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"].startswith("alft_at_")
    assert body["refresh_token"].startswith("alft_rt_")
    assert body["token_type"] == "Bearer"
    assert "expires_at" in body


# ---- Browser approval surface ----------------------------------------


def test_approval_page_requires_auth():
    client = Client()
    row, sid = device_flow.create_session()
    r = client.get(f"/app/cli/auth/device/{sid}/")
    assert r.status_code == 302
    login = urlsplit(r["Location"])
    assert login.path == "/app/auth1/login"
    assert parse_qs(login.query)["next"] == [f"/app/cli/auth/device/{sid}/"]


def test_approval_page_renders_for_authed_user():
    user = _make_user(password="pw")
    org = Organization.objects.create(name="X", slug="x")
    _make_member(user, org)
    row, sid = device_flow.create_session(client_label="astro cli")

    client = Client()
    client.force_login(user)
    r = client.get(f"/app/cli/auth/device/{sid}/")
    assert r.status_code == 200
    assert b"astro cli" in r.content
    assert b"Approve" in r.content
    assert b"agent-env-spec:write" in r.content
    assert b"secret:write" in r.content
    assert b"mcp:dispatch" in r.content
    assert b"mcp:write" in r.content
    assert b"project:write" in r.content
    assert b"workflow:write" in r.content
    assert b"workflow:trigger" in r.content
    assert b"secret:read" not in r.content


def test_non_cli_device_sessions_keep_read_only_scopes():
    for kind in ("mobile", "browser", "ide"):
        assert device_flow.token_scopes_for_client_kind(kind) == list(device_flow.DEFAULT_SCOPES)


def test_approval_post_approve_drives_state_machine():
    user = _make_user(password="pw")
    org = Organization.objects.create(name="X", slug="x")
    _make_member(user, org)
    row, sid = device_flow.create_session()

    client = Client()
    client.force_login(user)
    r = client.post(f"/app/cli/auth/device/{sid}/", data={"action": "approve"})
    assert r.status_code == 200
    row.refresh_from_db()
    assert row.state == DeviceFlowSession.STATE_APPROVED
    assert row.approved_user_id == user.id
    assert row.organization_id == org.id


def test_approval_post_deny_drives_state_machine():
    user = _make_user(password="pw")
    org = Organization.objects.create(name="X", slug="x")
    _make_member(user, org)
    row, sid = device_flow.create_session()

    client = Client()
    client.force_login(user)
    r = client.post(f"/app/cli/auth/device/{sid}/", data={"action": "deny"})
    assert r.status_code == 200
    row.refresh_from_db()
    assert row.state == DeviceFlowSession.STATE_DENIED


def test_approval_multi_org_requires_picker_choice():
    user = _make_user(password="pw")
    org1 = Organization.objects.create(name="Acme", slug="acme")
    org2 = Organization.objects.create(name="Beta", slug="beta")
    _make_member(user, org1)
    _make_member(user, org2)
    row, sid = device_flow.create_session()

    client = Client()
    client.force_login(user)
    # GET shows both orgs in the picker
    r = client.get(f"/app/cli/auth/device/{sid}/")
    assert b"Acme" in r.content and b"Beta" in r.content

    # POST without picking an org → 400 with error re-render
    r2 = client.post(f"/app/cli/auth/device/{sid}/", data={"action": "approve"})
    assert r2.status_code == 400

    # POST with a tenancy-valid choice succeeds + binds that org
    r3 = client.post(
        f"/app/cli/auth/device/{sid}/",
        data={"action": "approve", "organization_id": str(org2.id)},
    )
    assert r3.status_code == 200
    row.refresh_from_db()
    assert row.organization_id == org2.id


def test_approval_rejects_non_member_org_choice():
    user = _make_user(password="pw")
    user_org = Organization.objects.create(name="Mine", slug="mine")
    other_org = Organization.objects.create(name="Other", slug="other")
    _make_member(user, user_org)
    # Second user-org so the multi-picker triggers (single org takes
    # the auto-pick path which doesn't read the form field at all).
    extra_org = Organization.objects.create(name="Extra", slug="extra")
    _make_member(user, extra_org)
    row, sid = device_flow.create_session()

    client = Client()
    client.force_login(user)
    # Try to bind to ``other_org`` which the user isn't a member of:
    # the view drops the choice, falls back to the picker error path.
    r = client.post(
        f"/app/cli/auth/device/{sid}/",
        data={"action": "approve", "organization_id": str(other_org.id)},
    )
    assert r.status_code == 400
    row.refresh_from_db()
    # State stays pending: we refused the malformed approval.
    assert row.state == DeviceFlowSession.STATE_PENDING


def test_approval_unknown_session_returns_404():
    user = _make_user(password="pw")
    client = Client()
    client.force_login(user)
    r = client.get("/app/cli/auth/device/nonexistent/")
    assert r.status_code == 404
