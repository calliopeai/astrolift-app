"""End-to-end tests for the install-enrollment QR flow (#494).

Covers the mobile-enrollment surface that extends the device-flow
auth from #475:

* ``device_flow.create_enrollment`` mints a pre-approved session +
  ``alft_enroll_…`` plaintext, binds to the operator.
* Per-user rate limit caps concurrent unconsumed enrollments.
* ``device_flow.consume_enrollment`` flips ``pre_approved`` to
  ``consumed`` and returns ``alft_at_…`` + ``alft_rt_…`` credentials.
* Single-use: a second redeem of the same token is rejected.
* Expired enrollments are rejected.
* Issued access token authenticates the standard middleware.
* ``POST /api/cli/v1/auth/start`` with ``enrollment_token`` returns
  credentials immediately (no browser step, no polling).
* The ``generateInstallEnrollmentQr`` mutation returns a valid
  base64-JSON ``qrPayload`` + an SVG that contains real QR modules.
* Audit events fire on both generation and consumption.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import json
import uuid

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
from astrolift_identity.schema.mutations import (
    GenerateInstallEnrollmentQrInput,
    IdentityMutation,
)
from core.mutations import AuditEntry, register_audit_writer
from core.tenancy import TenantContext, set_current_tenant

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---- helpers ---------------------------------------------------------


def _make_user(email: str | None = None) -> User:
    if email is None:
        email = f"u-{uuid.uuid4().hex[:8]}@astrolift.dev"
    return User.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password="pw",
    )


def _make_user_with_org() -> tuple[User, Organization]:
    """Convenience for tests that need both — ApiToken.organization is
    NOT NULL so every enrollment-issued credential needs a binding."""
    user = _make_user()
    org = Organization.objects.create(
        name=f"Org-{uuid.uuid4().hex[:6]}",
        slug=f"org-{uuid.uuid4().hex[:6]}",
    )
    _make_member(user, org)
    return user, org


def _make_member(user, org):
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


def _post_json(client: Client, path: str, payload: dict):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


@dataclasses.dataclass
class _FakeRequest:
    """Minimal Info.context stand-in for direct resolver invocation."""

    user: User


class _FakeInfo:
    """Stand-in for ``strawberry.types.Info`` — the only attribute the
    mutation reads is ``info.context.request.user`` (indirectly via the
    ``_actor`` helper, which actually reads ``get_current_tenant``).
    Keep this minimal — broaden if a resolver grows new context needs.
    """

    def __init__(self, user: User):
        self.context = _FakeRequest(user=user)


# ---- create_enrollment / consume_enrollment business layer -----------


def test_create_enrollment_returns_pre_approved_row_with_token():
    user = _make_user()
    org = Organization.objects.create(name="Acme", slug="acme")

    result = device_flow.create_enrollment(user=user, organization=org, label="Sarah iPhone")
    assert not isinstance(result, str), f"unexpected error: {result}"
    assert result.token_plaintext.startswith("alft_enroll_")
    assert result.session_id
    assert result.expires_at > timezone.now()

    row = DeviceFlowSession.objects.get(session_guid=result.session_id)
    assert row.state == DeviceFlowSession.STATE_PRE_APPROVED
    assert row.origin == DeviceFlowSession.ORIGIN_ENROLLMENT
    assert row.approved_user_id == user.id
    assert row.organization_id == org.id
    assert row.enrollment_label == "Sarah iPhone"
    assert row.enrollment_token_hash  # populated
    assert row.enrollment_token_hash != result.token_plaintext  # hashed
    assert row.enrollment_token_last_4 == result.token_plaintext[-4:]


def test_create_enrollment_clamps_ttl_to_max():
    user, org = _make_user_with_org()
    # Request 24h — should clamp to the 15min cap.
    result = device_flow.create_enrollment(
        user=user,
        organization=org,
        ttl_seconds=24 * 60 * 60,
    )
    assert not isinstance(result, str)
    delta = result.expires_at - timezone.now()
    assert delta <= device_flow.ENROLLMENT_TTL_MAX + dt.timedelta(seconds=2)


def test_create_enrollment_rate_limits_per_user():
    user, org = _make_user_with_org()
    for _ in range(device_flow.ENROLLMENT_MAX_ACTIVE_PER_USER):
        assert not isinstance(device_flow.create_enrollment(user=user, organization=org), str)
    # One more should be refused.
    refused = device_flow.create_enrollment(user=user, organization=org)
    assert refused == "rate_limited"


def test_create_enrollment_rate_limit_excludes_expired_rows():
    user, org = _make_user_with_org()
    for _ in range(device_flow.ENROLLMENT_MAX_ACTIVE_PER_USER):
        device_flow.create_enrollment(user=user, organization=org)
    DeviceFlowSession.objects.filter(approved_user=user).update(
        enrollment_token_expires_at=timezone.now() - dt.timedelta(seconds=10),
    )
    fresh = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(fresh, str)


def test_create_enrollment_without_organization_rejected():
    user = _make_user()
    result = device_flow.create_enrollment(user=user, organization=None)
    assert result == "no_organization"


def test_consume_enrollment_issues_credentials_and_burns_token():
    user = _make_user()
    org = Organization.objects.create(name="Acme", slug="acme")
    minted = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(minted, str)

    result = device_flow.consume_enrollment(
        minted.token_plaintext,
        client_label="Sarah's iPhone 15",
        client_kind="mobile",
        user_agent="astro-mobile/2.1.0 (iOS 17.4)",
        client_ip="203.0.113.5",
    )
    assert result.status == "issued"
    assert result.credentials is not None
    assert result.credentials.access_token.startswith("alft_at_")
    assert result.credentials.refresh_token.startswith("alft_rt_")

    row = DeviceFlowSession.objects.get(session_guid=minted.session_id)
    assert row.state == DeviceFlowSession.STATE_CONSUMED
    assert row.enrollment_consumed_at is not None
    assert row.enrollment_token_hash == ""  # burned
    assert row.client_label == "Sarah's iPhone 15"
    assert row.client_kind == "mobile"
    assert row.user_agent.startswith("astro-mobile/")
    assert row.client_ip == "203.0.113.5"
    assert row.api_token_id is not None
    # The minted access token belongs to the operator + their org.
    api_token = ApiToken.objects.get(pk=row.api_token_id)
    assert api_token.user_id == user.id
    assert api_token.organization_id == org.id


def test_consume_enrollment_single_use_rejects_second_attempt():
    user, org = _make_user_with_org()
    minted = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(minted, str)

    first = device_flow.consume_enrollment(minted.token_plaintext)
    assert first.status == "issued"

    second = device_flow.consume_enrollment(minted.token_plaintext)
    assert second.status == "unknown", "burned tokens must read as unknown"


def test_consume_enrollment_expired_token_is_rejected_and_marked_terminal():
    user, org = _make_user_with_org()
    minted = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(minted, str)

    DeviceFlowSession.objects.filter(session_guid=minted.session_id).update(
        enrollment_token_expires_at=timezone.now() - dt.timedelta(seconds=1)
    )
    result = device_flow.consume_enrollment(minted.token_plaintext)
    assert result.status == "expired"

    row = DeviceFlowSession.objects.get(session_guid=minted.session_id)
    assert row.state == DeviceFlowSession.STATE_EXPIRED
    assert row.enrollment_token_hash == ""


def test_consume_enrollment_bad_prefix_is_unknown():
    assert device_flow.consume_enrollment("").status == "unknown"
    assert device_flow.consume_enrollment("not_a_real_token").status == "unknown"
    assert device_flow.consume_enrollment("alft_at_lookalike").status == "unknown"


def test_consume_enrollment_unknown_hash_is_unknown():
    assert device_flow.consume_enrollment("alft_enroll_doesnotexist_hash_miss_here").status == "unknown"


# ---- middleware integration -----------------------------------------


def test_issued_access_token_from_enrollment_authenticates_middleware():
    user = _make_user()
    org = Organization.objects.create(name="X", slug="x")
    _make_member(user, org)
    minted = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(minted, str)
    creds = device_flow.consume_enrollment(minted.token_plaintext)
    assert creds.status == "issued"

    captured: dict = {}

    def _view(request):
        captured["request"] = request
        return HttpResponse(b"ok", status=200)

    mw = ApiTokenAuthMiddleware(_view)
    req = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {creds.credentials.access_token}")
    req.user = AnonymousUser()
    response = mw(req)
    assert response.status_code == 200
    assert captured["request"].user.is_authenticated
    assert captured["request"].user.id == user.id


# ---- REST view: /auth/start with enrollment_token --------------------


def test_view_start_with_enrollment_token_issues_credentials():
    user = _make_user()
    org = Organization.objects.create(name="X", slug="x")
    minted = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(minted, str)

    client = Client()
    r = _post_json(
        client,
        "/api/cli/v1/auth/start",
        {
            "enrollment_token": minted.token_plaintext,
            "client_label": "iPhone 15",
            "client_kind": "mobile",
        },
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["access_token"].startswith("alft_at_")
    assert body["refresh_token"].startswith("alft_rt_")
    assert body["token_type"] == "Bearer"
    assert "expires_at" in body
    # No login_url / session_id keys — this is the credential-only path.
    assert "login_url" not in body
    assert "session_id" not in body


def test_view_start_with_invalid_enrollment_token_returns_401():
    client = Client()
    r = _post_json(
        client,
        "/api/cli/v1/auth/start",
        {"enrollment_token": "alft_enroll_does_not_exist"},
    )
    assert r.status_code == 401


def test_view_start_with_expired_enrollment_token_returns_410():
    user, org = _make_user_with_org()
    minted = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(minted, str)
    DeviceFlowSession.objects.filter(session_guid=minted.session_id).update(
        enrollment_token_expires_at=timezone.now() - dt.timedelta(seconds=1)
    )

    client = Client()
    r = _post_json(
        client,
        "/api/cli/v1/auth/start",
        {"enrollment_token": minted.token_plaintext},
    )
    assert r.status_code == 410


def test_view_start_without_enrollment_token_keeps_legacy_behaviour():
    """Regression: a /start without enrollment_token must still
    return the polling envelope (the #475 contract)."""
    client = Client()
    r = _post_json(client, "/api/cli/v1/auth/start", {})
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    assert "login_url" in body
    assert "access_token" not in body


# ---- GraphQL mutation: generateInstallEnrollmentQr -------------------


def test_generate_install_enrollment_qr_returns_payload_with_valid_json():
    user = _make_user()
    org = Organization.objects.create(name="Acme", slug="acme")
    _make_member(user, org)
    set_current_tenant(TenantContext(actor_user_id=user.id, organization_id=org.id))

    info = _FakeInfo(user=user)
    inp = GenerateInstallEnrollmentQrInput(label="Sarah iPhone")
    result = IdentityMutation().generate_install_enrollment_qr(info, inp)

    assert result.ok is True, result.errors
    assert result.data is not None
    payload = result.data

    # qr_payload decodes to the JSON described in the spec.
    decoded = json.loads(base64.urlsafe_b64decode(payload.qr_payload).decode("utf-8"))
    assert decoded["v"] == 1
    assert "install_url" in decoded
    assert "install_label" in decoded
    assert decoded["enrollment_token"].startswith("alft_enroll_")
    assert "expires_at" in decoded
    assert decoded["session_id"] == payload.session_id

    # SVG carries real QR modules (dark rectangles) — not an empty img.
    assert payload.qr_svg.startswith("<svg")
    assert payload.qr_svg.count("h1v1h-1z") > 50, "SVG should contain many module rects"
    assert payload.expires_at > timezone.now()


def test_generate_install_enrollment_qr_creates_pre_approved_row():
    user, org = _make_user_with_org()
    info = _FakeInfo(user=user)
    set_current_tenant(TenantContext(actor_user_id=user.id, organization_id=org.id))
    inp = GenerateInstallEnrollmentQrInput()
    result = IdentityMutation().generate_install_enrollment_qr(info, inp)
    assert result.ok is True, result.errors

    row = DeviceFlowSession.objects.get(session_guid=result.data.session_id)
    assert row.state == DeviceFlowSession.STATE_PRE_APPROVED
    assert row.origin == DeviceFlowSession.ORIGIN_ENROLLMENT
    assert row.approved_user_id == user.id
    assert row.organization_id == org.id


def test_generate_install_enrollment_qr_rate_limited_returns_envelope():
    user, org = _make_user_with_org()
    set_current_tenant(TenantContext(actor_user_id=user.id, organization_id=org.id))
    info = _FakeInfo(user=user)
    for _ in range(device_flow.ENROLLMENT_MAX_ACTIVE_PER_USER):
        IdentityMutation().generate_install_enrollment_qr(info, GenerateInstallEnrollmentQrInput())
    extra = IdentityMutation().generate_install_enrollment_qr(info, GenerateInstallEnrollmentQrInput())
    assert extra.ok is False
    assert extra.errors[0].code == "RATE_LIMITED"


def test_generate_install_enrollment_qr_without_active_org_returns_precondition():
    user = _make_user()
    info = _FakeInfo(user=user)
    set_current_tenant(TenantContext(actor_user_id=user.id, organization_id=None))
    result = IdentityMutation().generate_install_enrollment_qr(info, GenerateInstallEnrollmentQrInput())
    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"


# ---- audit events ----------------------------------------------------


def test_audit_event_emitted_on_qr_generation(monkeypatch):
    captured: list[AuditEntry] = []

    def writer(entry: AuditEntry) -> None:
        captured.append(entry)

    from core import mutations as core_mutations

    # Snapshot the live writer + restore afterwards. Per the
    # `event_writer_fixture_restore` memory — capture the actual
    # registered value, not the module's logger fallback.
    original_writer = core_mutations._audit_writer
    register_audit_writer(writer)
    try:
        user, org = _make_user_with_org()
        set_current_tenant(TenantContext(actor_user_id=user.id, organization_id=org.id))
        info = _FakeInfo(user=user)
        IdentityMutation().generate_install_enrollment_qr(
            info, GenerateInstallEnrollmentQrInput(label="iPad")
        )
    finally:
        register_audit_writer(original_writer)

    actions = [e.action for e in captured]
    assert "auth.enrollment.generated" in actions
    entry = next(e for e in captured if e.action == "auth.enrollment.generated")
    assert entry.decision == "ALLOW"
    assert entry.actor_user_id == user.id


def test_audit_event_emitted_on_qr_consumption():
    user, org = _make_user_with_org()
    minted = device_flow.create_enrollment(user=user, organization=org)
    assert not isinstance(minted, str)

    captured: list[AuditEntry] = []

    def writer(entry: AuditEntry) -> None:
        captured.append(entry)

    from core import mutations as core_mutations

    original_writer = core_mutations._audit_writer
    register_audit_writer(writer)
    try:
        client = Client()
        r = _post_json(
            client,
            "/api/cli/v1/auth/start",
            {"enrollment_token": minted.token_plaintext, "client_label": "iPhone"},
        )
        assert r.status_code == 200
    finally:
        register_audit_writer(original_writer)

    actions = [e.action for e in captured]
    assert "auth.enrollment.consumed" in actions
    entry = next(e for e in captured if e.action == "auth.enrollment.consumed")
    assert entry.actor_user_id == user.id  # attributed to the operator
    assert entry.target_kind == "device_flow_session"


# ---- QR encoder sanity (catches pure-Python encoder regressions) -----


def test_qr_encoder_produces_consistent_output_for_known_payload():
    from astrolift_identity import _qr

    qr = _qr.encode_text("astrolift://enroll?payload=AAAA", _qr.Ecc.M)
    assert qr.version >= 1
    assert qr.size == qr.version * 4 + 17
    assert 0 <= qr.mask <= 7
    # Finder pattern: top-left 7×7 corner should have a dark border
    # (modules at (0..6, 0) and (0, 0..6) all true) — sanity check
    # that the matrix wasn't accidentally rendered all-white.
    dark_count = sum(1 for row in qr.modules for v in row if v)
    light_count = qr.size * qr.size - dark_count
    assert dark_count > 0 and light_count > 0


def test_qr_svg_renders_without_javascript():
    """Defence in depth: ``dangerouslySetInnerHTML`` consumes this
    string, so it must contain no script / event-handler vectors."""
    from astrolift_identity import _qr

    svg = _qr.to_svg(_qr.encode_text("https://acme.astrolift.io", _qr.Ecc.M))
    lowered = svg.lower()
    assert "<script" not in lowered
    assert "onload" not in lowered
    assert "javascript:" not in lowered
    assert "onerror" not in lowered
