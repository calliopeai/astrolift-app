"""SSO step-up auth (#526) — verifier + envelope + redirect coverage.

Acceptance covers the SSO branch of step-up:

* SSO-minted session hitting a sensitive mutation returns
  ``STEP_UP_REQUIRED`` with ``supportedMethods=["sso"]``.
* Password-minted session keeps the legacy ``["password"]`` envelope
  (no regression on the #487 flow).
* ``elevate_sso_start`` redirects to the IdP with ``prompt=login``
  and ``max_age=0`` plus the one-shot state / nonce stashed on the
  session.
* ``elevate_sso_callback`` with a fresh ``auth_time`` elevates the
  session and 302s back to the safe return URL.
* Stale ``auth_time`` is rejected (replay defence) + audited.
* State mismatch on callback is rejected + audited (CSRF defence).
* Cancellation (``?error=...`` from the IdP) is rejected gracefully.
* ``is_safe_return_url`` rejects open-redirect attempts.
* ``METHOD_SSO`` is in ``KNOWN_METHODS`` and the default verifier
  refuses to elevate via the password mutation.
* Elevation TTL expiry causes the next sensitive mutation to
  re-prompt with the SSO-shaped envelope.
* ``record_session`` propagates ``login_method`` from the session
  bag to the sidecar row.
"""

from __future__ import annotations

import datetime as dt
import time
from types import SimpleNamespace
from unittest import mock

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from astrolift_identity.models import (
    LoginMethod,
    Organization,
    Role,
)
from astrolift_identity.schema.mutations import (
    ElevateAdminSessionInput,
    GrantRoleInput,
    IdentityMutation,
)
from astrolift_identity.session_elevation import (
    KNOWN_METHODS,
    METHOD_PASSWORD,
    METHOD_SSO,
    default_sso_verifier,
    elevate,
    is_elevated,
    reset_credential_verifier_for_tests,
)
from astrolift_identity.sessions import (
    SESSION_LOGIN_METHOD_KEY,
    record_session,
)
from astrolift_identity.step_up_sso import (
    SESSION_SSO_AUTH_TIME_KEY,
    SESSION_SSO_NONCE_KEY,
    SESSION_SSO_RETURN_KEY,
    SESSION_SSO_STATE_KEY,
    elevate_sso_callback,
    elevate_sso_start,
    freshness_window_seconds,
    is_safe_return_url,
)
from core.mutations import (
    AuditEntry,
    ErrorCode,
    register_audit_writer,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()


# ---- harness -----------------------------------------------------------


def _admin_user(email: str | None = None, *, password: str | None = None) -> User:
    if email is None:
        import uuid

        email = f"step-up-sso-{uuid.uuid4().hex[:8]}@astrolift.dev"
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"username": email.split("@")[0]},
    )
    if password:
        user.set_password(password)
        user.save(update_fields=["password"])
    else:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


class _FakeSession(dict):
    """Mimics ``HttpRequest.session`` for direct-call mutation tests."""

    modified = False
    session_key = "fake-session-key"

    def save(self) -> None:  # pragma: no cover — no-op for tests
        pass


def _info(user, *, session: dict | None = None):
    session = session if session is not None else _FakeSession()
    request = SimpleNamespace(user=user, session=session, _api_token=None)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


@pytest.fixture
def audit_capture():
    captured: list[AuditEntry] = []
    from core.mutations import _audit_writer as _orig_writer  # noqa: PLC2701

    def _writer(entry: AuditEntry) -> None:
        captured.append(entry)

    register_audit_writer(_writer)
    yield captured
    register_audit_writer(_orig_writer)


@pytest.fixture(autouse=True)
def _restore_verifier():
    """Re-install the composed verifier (password + sso) per test."""
    from astrolift_identity.session_elevation import (
        compose_verifiers,
        default_password_verifier,
        register_credential_verifier,
    )

    register_credential_verifier(compose_verifiers(default_password_verifier, default_sso_verifier))
    yield
    reset_credential_verifier_for_tests()
    register_credential_verifier(compose_verifiers(default_password_verifier, default_sso_verifier))


# ---- verifier / known-methods sanity ----------------------------------


def test_known_methods_includes_sso():
    """Adding ``sso`` to KNOWN_METHODS lets the mutation surface a
    typed deny instead of a VALIDATION envelope. The actual ceremony
    runs through the SSO redirect endpoint, not the mutation."""
    assert METHOD_SSO in KNOWN_METHODS


def test_default_sso_verifier_rejects_unconditionally():
    """SSO elevation cannot complete through the mutation API — the
    verifier is a deny-stub so a misconfigured FE that POSTs
    ``method=sso`` to elevateAdminSession surfaces PERMISSION_DENIED
    instead of silently elevating with no credential check."""
    user = _admin_user()
    assert default_sso_verifier(user, METHOD_SSO, "any-credential") is False
    # Other methods fall through to deny so compose_verifiers can
    # chain it without short-circuiting the password path.
    assert default_sso_verifier(user, METHOD_PASSWORD, "pw") is False


def test_elevate_admin_session_with_sso_method_returns_denied():
    """End-to-end: POSTing ``method=sso`` to the mutation hits the
    composed verifier (password + sso-deny) and surfaces a
    PERMISSION_DENIED envelope, not a 500 and not a silent elevate."""
    user = _admin_user(password="pw")
    session = _FakeSession()

    result = IdentityMutation().elevate_admin_session(
        _info(user, session=session),
        input=ElevateAdminSessionInput(method="sso", credential="anything"),
    )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert not is_elevated(session)


# ---- supported_methods plumbing on the deny envelope ------------------


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_sso_session_step_up_deny_carries_supported_methods_sso(permission_resolver):
    """The prod bug (#526): SSO user → sensitive mutation → modal had
    no actionable button because the deny envelope didn't tell the FE
    which credential family to collect. The fix surfaces
    ``supportedMethods=['sso']`` so the StepUpPrompt branches to the
    IdP redirect button."""
    org = Organization.objects.create(name="Acme", slug="acme")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user()  # unusable password — SSO-only
    target = _admin_user(email="grantee-sso@astrolift.dev")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()
    session[SESSION_LOGIN_METHOD_KEY] = "sso"

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin, session=session),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=role.guid,
                scope_kind="ORG",
                scope_guid=org.guid,
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.STEP_UP_REQUIRED.value
    assert result.errors[0].supported_methods == [METHOD_SSO]


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_password_session_step_up_deny_carries_supported_methods_password(permission_resolver):
    """No-regression: a password-minted session still gets the
    ``["password"]`` envelope so the existing prompt UI keeps working."""
    org = Organization.objects.create(name="Acme", slug="acme")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user(password="pw")
    target = _admin_user(email="grantee-pw@astrolift.dev")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()
    session[SESSION_LOGIN_METHOD_KEY] = "password"

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin, session=session),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=role.guid,
                scope_kind="ORG",
                scope_guid=org.guid,
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.STEP_UP_REQUIRED.value
    assert result.errors[0].supported_methods == [METHOD_PASSWORD]


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_unset_login_method_falls_back_to_password_envelope(permission_resolver):
    """Pre-#526 sessions don't carry the key; the deny envelope must
    still default to ``["password"]`` so legacy operators see the form
    they had before."""
    org = Organization.objects.create(name="Acme", slug="acme")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user(password="pw")
    target = _admin_user(email="grantee-legacy@astrolift.dev")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()  # no SESSION_LOGIN_METHOD_KEY

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin, session=session),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=role.guid,
                scope_kind="ORG",
                scope_guid=org.guid,
            ),
        )

    assert result.ok is False
    assert result.errors[0].supported_methods == [METHOD_PASSWORD]


def test_sso_elevation_satisfies_step_up_gate(permission_resolver):
    """An SSO session that did elevate via the redirect flow (i.e.
    ``elevate(method='sso')`` was called by the callback) bypasses
    the step-up gate just like a password elevation would. Same
    timer; same TTL; same downstream behaviour."""
    org = Organization.objects.create(name="Acme", slug="acme")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user()
    target = _admin_user(email="grantee-sso-elev@astrolift.dev")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()
    session[SESSION_LOGIN_METHOD_KEY] = "sso"
    elevate(session, method=METHOD_SSO, ttl_seconds=300)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin, session=session),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=role.guid,
                scope_kind="ORG",
                scope_guid=org.guid,
            ),
        )

    assert result.ok is True, result.errors


# ---- safe-return guard ------------------------------------------------


def test_is_safe_return_url_accepts_relative_paths():
    assert is_safe_return_url("/app/admin/")
    assert is_safe_return_url("/foo?bar=1")


def test_is_safe_return_url_rejects_open_redirects():
    """The classic open-redirect surface lives at the start of the
    elevate-sso flow — a caller can pass ?return=//evil.example.com/x
    and the callback would 302 there. Reject schemes + hosts."""
    assert not is_safe_return_url("//evil.example.com/x")
    assert not is_safe_return_url("https://evil.example.com")
    assert not is_safe_return_url("javascript:alert(1)")
    assert not is_safe_return_url("")
    assert not is_safe_return_url(None)
    assert not is_safe_return_url("relative/path")  # no leading /


# ---- elevate_sso_start ------------------------------------------------


@pytest.fixture
def rf():
    return RequestFactory()


def test_elevate_sso_start_requires_authentication(rf):
    """Anonymous callers get 401 — the redirect dance shouldn't even
    begin without an authenticated session to elevate."""
    request = rf.get("/app/auth1/elevate-sso/?return=/app/admin/")
    request.user = SimpleNamespace(is_authenticated=False)
    request.session = _FakeSession()
    response = elevate_sso_start(request)
    assert response.status_code == 401


def test_elevate_sso_start_redirects_to_idp_with_prompt_and_max_age(rf):
    """The required-for-correctness query params: ``prompt=login``
    + ``max_age=0`` force the IdP to assert a fresh ``auth_time``
    even if the operator has an active IdP cookie."""
    user = _admin_user()
    request = rf.get("/app/auth1/elevate-sso/?return=/app/admin/")
    request.user = user
    request.session = _FakeSession()

    with mock.patch("astrolift_identity.step_up_sso._oauth.auth0_stepup.authorize_redirect") as mock_redirect:
        from django.http import HttpResponseRedirect

        mock_redirect.return_value = HttpResponseRedirect("https://idp.example/authorize?prompt=login")
        elevate_sso_start(request)

    assert mock_redirect.called, "expected authorize_redirect to fire"
    _, kwargs = mock_redirect.call_args
    assert kwargs.get("prompt") == "login"
    assert kwargs.get("max_age") == 0
    # state + nonce + return stashed on the session for the callback
    assert request.session.get(SESSION_SSO_STATE_KEY)
    assert request.session.get(SESSION_SSO_NONCE_KEY)
    assert request.session.get(SESSION_SSO_RETURN_KEY) == "/app/admin/"


def test_elevate_sso_start_clamps_unsafe_return(rf):
    """A malicious ``?return=//evil`` must not be stashed verbatim;
    the safe-return normalizer drops it and the callback can only
    redirect to the app root."""
    user = _admin_user()
    request = rf.get("/app/auth1/elevate-sso/?return=//evil.example.com/x")
    request.user = user
    request.session = _FakeSession()

    with mock.patch("astrolift_identity.step_up_sso._oauth.auth0_stepup.authorize_redirect") as mock_redirect:
        from django.http import HttpResponseRedirect

        mock_redirect.return_value = HttpResponseRedirect("https://idp.example/authorize")
        elevate_sso_start(request)

    stashed = request.session.get(SESSION_SSO_RETURN_KEY)
    assert stashed != "//evil.example.com/x"
    assert not stashed.startswith("//")


# ---- elevate_sso_callback ---------------------------------------------


def _set_up_callback_session(user, *, return_to="/app/admin/", state="state-xyz", nonce="nonce-xyz"):
    session = _FakeSession()
    session[SESSION_SSO_STATE_KEY] = state
    session[SESSION_SSO_NONCE_KEY] = nonce
    session[SESSION_SSO_RETURN_KEY] = return_to
    return session


def test_callback_with_fresh_auth_time_elevates_and_redirects(rf, audit_capture):
    """Happy path: state matches, nonce matches, auth_time is within
    the freshness window. Session elevates, redirect to return URL,
    success audited."""
    user = _admin_user()
    session = _set_up_callback_session(user)
    request = rf.get("/app/auth1/elevate-sso/callback/?state=state-xyz&code=abc")
    request.user = user
    request.session = session

    fresh_auth_time = int(time.time()) - 5  # 5s old, well inside window
    with mock.patch(
        "astrolift_identity.step_up_sso._oauth.auth0_stepup.authorize_access_token",
        return_value={
            "userinfo": {"auth_time": fresh_auth_time, "nonce": "nonce-xyz"},
            "id_token": "fake.id.token",
        },
    ):
        response = elevate_sso_callback(request)

    assert response.status_code == 302
    assert response["Location"] == "/app/admin/"
    assert is_elevated(session)
    assert session.get(SESSION_SSO_AUTH_TIME_KEY) == fresh_auth_time
    # State + nonce are consumed (one-shot).
    assert SESSION_SSO_STATE_KEY not in session
    assert SESSION_SSO_NONCE_KEY not in session
    actions = [e.action for e in audit_capture]
    assert "auth.elevate_admin.sso.success" in actions


def test_callback_with_stale_auth_time_rejected_and_audited(rf, audit_capture):
    """Replay defence: a cached id_token with an auth_time older than
    the freshness window is rejected even if state + nonce match."""
    user = _admin_user()
    session = _set_up_callback_session(user)
    request = rf.get("/app/auth1/elevate-sso/callback/?state=state-xyz&code=abc")
    request.user = user
    request.session = session

    stale_auth_time = int(time.time()) - freshness_window_seconds() - 60
    with mock.patch(
        "astrolift_identity.step_up_sso._oauth.auth0_stepup.authorize_access_token",
        return_value={
            "userinfo": {"auth_time": stale_auth_time, "nonce": "nonce-xyz"},
        },
    ):
        response = elevate_sso_callback(request)

    assert response.status_code == 302
    assert "stepUp=stale_auth_time" in response["Location"]
    assert not is_elevated(session)
    actions = [e.action for e in audit_capture]
    assert "auth.elevate_admin.sso.stale_auth_time" in actions


def test_callback_with_state_mismatch_rejected_and_audited(rf, audit_capture):
    """CSRF defence: the state in the query string must match what
    we stashed at start time. Mismatch → fail + audit, no elevation."""
    user = _admin_user()
    session = _set_up_callback_session(user, state="real-state")
    request = rf.get("/app/auth1/elevate-sso/callback/?state=tampered-state&code=abc")
    request.user = user
    request.session = session

    response = elevate_sso_callback(request)
    assert response.status_code == 302
    assert "stepUp=state_mismatch" in response["Location"]
    assert not is_elevated(session)
    actions = [e.action for e in audit_capture]
    assert "auth.elevate_admin.sso.state_mismatch" in actions


def test_callback_with_idp_error_response_rejected(rf, audit_capture):
    """If the operator cancels at the IdP, the IdP redirects with
    ``?error=access_denied`` — we gracefully fail + bounce back rather
    than 500 on the missing ``state`` / ``code``."""
    user = _admin_user()
    session = _set_up_callback_session(user)
    request = rf.get("/app/auth1/elevate-sso/callback/?error=access_denied&error_description=user+cancelled")
    request.user = user
    request.session = session

    response = elevate_sso_callback(request)
    assert response.status_code == 302
    assert "stepUp=elevation_cancelled" in response["Location"]
    assert not is_elevated(session)
    actions = [e.action for e in audit_capture]
    assert "auth.elevate_admin.sso.error_response" in actions


def test_callback_missing_auth_time_rejected(rf, audit_capture):
    """A token without auth_time at all (some IdP mis-configurations
    swallow the claim) must be rejected — we can't prove freshness
    without it."""
    user = _admin_user()
    session = _set_up_callback_session(user)
    request = rf.get("/app/auth1/elevate-sso/callback/?state=state-xyz&code=abc")
    request.user = user
    request.session = session

    with mock.patch(
        "astrolift_identity.step_up_sso._oauth.auth0_stepup.authorize_access_token",
        return_value={"userinfo": {"nonce": "nonce-xyz"}},
    ):
        response = elevate_sso_callback(request)

    assert response.status_code == 302
    assert "stepUp=missing_auth_time" in response["Location"]
    assert not is_elevated(session)
    actions = [e.action for e in audit_capture]
    assert "auth.elevate_admin.sso.missing_auth_time" in actions


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_callback_after_elevation_expiry_reprompts_with_sso_envelope(permission_resolver):
    """An SSO elevation that lapsed at the time of the next mutation
    gets the same STEP_UP_REQUIRED envelope, still carrying
    ``supportedMethods=['sso']`` so the FE knows to re-redirect."""
    org = Organization.objects.create(name="Acme", slug="acme")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user()
    target = _admin_user(email="grantee-expired-sso@astrolift.dev")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()
    session[SESSION_LOGIN_METHOD_KEY] = "sso"

    # Elevation expired 10s ago.
    from astrolift_identity.session_elevation import (
        SESSION_KEY_ELEVATED_UNTIL,
        SESSION_KEY_ELEVATION_METHOD,
    )

    session[SESSION_KEY_ELEVATED_UNTIL] = (timezone.now() - dt.timedelta(seconds=10)).isoformat()
    session[SESSION_KEY_ELEVATION_METHOD] = METHOD_SSO

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin, session=session),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=role.guid,
                scope_kind="ORG",
                scope_guid=org.guid,
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.STEP_UP_REQUIRED.value
    assert result.errors[0].supported_methods == [METHOD_SSO]


# ---- session sidecar ---------------------------------------------------


def test_record_session_persists_login_method_from_bag(rf):
    """When the SSO callback stamped the bag with ``login_method=sso``,
    ``record_session`` propagates it to the sidecar row so the operator-
    facing sessions list and any future server-side gates can see the
    auth method without re-reading the cookie jar."""
    from django.contrib.sessions.backends.db import SessionStore

    user = _admin_user()

    request = rf.get("/")
    request.user = user
    session_store = SessionStore()
    session_store[SESSION_LOGIN_METHOD_KEY] = "sso"
    session_store.create()
    request.session = session_store

    row = record_session(request)
    assert row is not None
    assert row.login_method == LoginMethod.SSO.value


def test_record_session_defaults_to_password_when_bag_missing(rf):
    """Pre-#526 sessions don't carry the key; the row defaults to
    ``password`` (legacy behaviour) so an upgrade-in-place install
    doesn't suddenly orphan its sessions."""
    from django.contrib.sessions.backends.db import SessionStore

    user = _admin_user()
    request = rf.get("/")
    request.user = user
    session_store = SessionStore()
    session_store.create()
    request.session = session_store

    row = record_session(request)
    assert row is not None
    assert row.login_method == LoginMethod.PASSWORD.value
