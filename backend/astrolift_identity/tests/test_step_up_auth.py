"""Step-up auth (#487) — mutation + decorator coverage.

Acceptance covers eight branches:

* elevate with correct password → success envelope + session timer set
* elevate with wrong password → permission-denied envelope + audit row
* elevate with unknown method → validation envelope
* elevate without auth → permission-denied envelope
* sensitive mutation without elevation → STEP_UP_REQUIRED envelope
* sensitive mutation with elevation → mutation actually runs
* TTL expiry → next call re-prompts
* deelevate → previously_elevated bit + subsequent call re-prompts
* non-sensitive mutation unaffected by missing elevation
* TTL clamping at the configured max
* astroliftElevationStatus query reflects elevation
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Member, Organization, Role, RoleBinding, Team
from astrolift_identity.schema.mutations import (
    BulkRevokeRoleBindingsInput,
    CreateTeamInput,
    ElevateAdminSessionInput,
    GrantRoleInput,
    IdentityMutation,
)
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_identity.session_elevation import (
    KNOWN_METHODS,
    METHOD_PASSWORD,
    METHOD_WEBAUTHN,
    SESSION_KEY_ELEVATED_UNTIL,
    SESSION_KEY_ELEVATION_METHOD,
    clamp_ttl_seconds,
    deelevate,
    elevate,
    get_status,
    is_elevated,
    reset_credential_verifier_for_tests,
)
from astrolift_identity.step_up import requires_elevation
from core.mutations import (
    AuditEntry,
    ErrorCode,
    MutationResult,
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

        email = f"step-up-{uuid.uuid4().hex[:8]}@astrolift.dev"
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"username": email.split("@")[0]},
    )
    if password:
        user.set_password(password)
        user.save(update_fields=["password"])
    return user


class _FakeSession(dict):
    """Stand-in for ``HttpRequest.session`` — Django's SessionStore
    is a dict-like with a ``modified`` attribute; the elevation
    helpers only need the dict surface."""

    modified = False


def _info(user, *, session: dict | None = None):
    """Build a Strawberry-shaped ``Info`` proxy with a fake request."""
    session = session if session is not None else _FakeSession()
    request = SimpleNamespace(user=user, session=session)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


@pytest.fixture
def audit_capture():
    """Capture every emitted AuditEntry for assertion."""
    captured: list[AuditEntry] = []
    from core.mutations import _audit_writer as _orig_writer  # noqa: PLC2701

    def _writer(entry: AuditEntry) -> None:
        captured.append(entry)

    register_audit_writer(_writer)
    yield captured
    register_audit_writer(_orig_writer)


@pytest.fixture(autouse=True)
def _restore_verifier():
    """Each test re-installs the default password verifier; the
    deny-all fallback would break every elevate test otherwise."""
    from astrolift_identity.session_elevation import (
        default_password_verifier,
        register_credential_verifier,
    )

    register_credential_verifier(default_password_verifier)
    yield
    reset_credential_verifier_for_tests()
    register_credential_verifier(default_password_verifier)


# ---- pure unit: session helpers ---------------------------------------


def test_get_status_on_fresh_session_is_unelevated():
    status = get_status(_FakeSession())
    assert status.elevated is False
    assert status.elevated_until is None
    assert status.seconds_remaining == 0
    assert status.method is None


def test_elevate_then_get_status_round_trips():
    session = _FakeSession()
    now = timezone.now()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300, now=now)
    status = get_status(session, now=now + dt.timedelta(seconds=10))
    assert status.elevated is True
    assert status.method == METHOD_PASSWORD
    assert 280 <= status.seconds_remaining <= 300


def test_elevate_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown elevation method"):
        elevate(_FakeSession(), method="sms")


def test_clamp_ttl_caps_at_max_and_floors_at_one():
    assert clamp_ttl_seconds(60) == 60
    # Anything above the max gets silently clamped; the default
    # STEP_UP_AUTH_MAX_TTL_SECONDS is 900 (15 min).
    assert clamp_ttl_seconds(99999) <= 900
    assert clamp_ttl_seconds(0) == 1
    assert clamp_ttl_seconds(-5) == 1
    assert clamp_ttl_seconds(None) >= 1


def test_deelevate_clears_keys_and_session_marked_modified():
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=60)
    assert SESSION_KEY_ELEVATED_UNTIL in session
    deelevate(session)
    assert SESSION_KEY_ELEVATED_UNTIL not in session
    assert SESSION_KEY_ELEVATION_METHOD not in session
    assert is_elevated(session) is False


def test_lapsed_elevation_is_treated_as_unelevated():
    session = _FakeSession()
    now = timezone.now()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=10, now=now)
    # Walk the clock past the expiry
    later = now + dt.timedelta(seconds=20)
    status = get_status(session, now=later)
    assert status.elevated is False
    assert status.seconds_remaining == 0


def test_corrupt_iso_string_treated_as_unelevated():
    session = _FakeSession()
    session[SESSION_KEY_ELEVATED_UNTIL] = "not-a-date"
    assert get_status(session).elevated is False


# ---- mutation: elevateAdminSession ------------------------------------


def test_elevate_with_correct_password_sets_session_and_returns_payload(audit_capture):
    user = _admin_user(password="hunter2!")
    session = _FakeSession()

    result = IdentityMutation().elevate_admin_session(
        _info(user, session=session),
        input=ElevateAdminSessionInput(method="password", credential="hunter2!", ttl_seconds=120),
    )

    assert result.ok is True, result.errors
    assert result.data.method == "password"
    assert result.data.seconds_remaining == 120
    assert isinstance(result.data.elevated_until, dt.datetime)
    # Session bag has the timer set
    assert is_elevated(session)
    # Success audit row was emitted by @mutation_audit
    actions = [e.action for e in audit_capture]
    assert "auth.elevate_admin" in actions


def test_elevate_with_wrong_password_returns_denied_and_audits_failure(audit_capture):
    user = _admin_user(password="correct-horse")
    session = _FakeSession()

    result = IdentityMutation().elevate_admin_session(
        _info(user, session=session),
        input=ElevateAdminSessionInput(method="password", credential="wrong"),
    )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert not is_elevated(session)
    actions = [e.action for e in audit_capture]
    assert "auth.elevate_admin.failed" in actions, actions


def test_elevate_unknown_method_returns_validation():
    user = _admin_user(password="pw")
    session = _FakeSession()

    result = IdentityMutation().elevate_admin_session(
        _info(user, session=session),
        input=ElevateAdminSessionInput(method="sms", credential="123"),
    )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "method"


def test_elevate_without_session_returns_precondition():
    user = _admin_user(password="pw")
    # Request without session (token-auth path).
    request = SimpleNamespace(user=user, session=None)
    info = SimpleNamespace(context=SimpleNamespace(request=request, user=user))

    result = IdentityMutation().elevate_admin_session(
        info,
        input=ElevateAdminSessionInput(method="password", credential="pw"),
    )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value


def test_elevate_unauthenticated_user_returns_denied():
    request = SimpleNamespace(user=None, session=_FakeSession())
    info = SimpleNamespace(context=SimpleNamespace(request=request, user=None))

    result = IdentityMutation().elevate_admin_session(
        info,
        input=ElevateAdminSessionInput(method="password", credential="anything"),
    )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


# ---- mutation: deelevateAdminSession ----------------------------------


def test_deelevate_clears_and_reports_previously_elevated():
    user = _admin_user(password="pw")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=60)
    assert is_elevated(session)

    result = IdentityMutation().deelevate_admin_session(_info(user, session=session))

    assert result.ok is True
    assert result.data.previously_elevated is True
    assert not is_elevated(session)


def test_deelevate_when_already_unelevated_is_noop():
    user = _admin_user(password="pw")
    session = _FakeSession()

    result = IdentityMutation().deelevate_admin_session(_info(user, session=session))

    assert result.ok is True
    assert result.data.previously_elevated is False


# ---- decorator: requires_elevation gating -----------------------------


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_sensitive_mutation_without_elevation_returns_step_up_required(permission_resolver, audit_capture):
    """grant_role is gated by @requires_elevation; an un-elevated
    session sees a STEP_UP_REQUIRED envelope, the resolver body
    never runs (no RoleBinding row created), and a deny is audited.

    The global REQUIRE_STEP_UP_AUTH Constance flag defaults to False
    (off) so small / SSO-only installs don't trip on every sensitive
    mutation. Flip it on for this test since the assertion is the
    flag-on enforcement path.
    """
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user(password="pw")
    target = _admin_user(email="grantee@astrolift.dev")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()  # un-elevated

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
    # The resolver body must not have run — no RoleBinding row.
    assert not RoleBinding.objects.filter(user=target, role=role).exists()
    # Audit row for the deny was emitted.
    actions = [e.action for e in audit_capture]
    assert "auth.step_up.denied" in actions
    # Strangely-shaped responses break the FE; assert envelope shape.
    assert isinstance(result, MutationResult) or hasattr(result, "ok")
    _ = team  # silence unused-local lint; row exists for FK realism


def test_sensitive_mutation_with_elevation_runs_resolver(permission_resolver):
    org = Organization.objects.create(name="Acme", slug="acme")
    Team.objects.create(organization=org, name="Eng", slug="eng")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user(password="pw")
    target = _admin_user(email="grantee@astrolift.dev")
    # #1183: grant_role now requires the target to already be a member of
    # the caller's org.
    Member.objects.create(
        user=target,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

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
    assert RoleBinding.objects.filter(user=target, role=role).exists()


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_elevation_expiry_re_prompts_on_next_call(permission_resolver, audit_capture):
    """An elevation that lapsed at the time of the next call gets the
    same STEP_UP_REQUIRED envelope as an un-elevated session."""
    org = Organization.objects.create(name="Acme", slug="acme")
    role = Role.objects.create(
        slug="developer",
        name="Developer",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    admin = _admin_user(password="pw")
    target = _admin_user(email="grantee@astrolift.dev")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()

    # Elevate, then hand-rewind the stored timer to be in the past.
    past = (timezone.now() - dt.timedelta(seconds=10)).isoformat()
    session[SESSION_KEY_ELEVATED_UNTIL] = past
    session[SESSION_KEY_ELEVATION_METHOD] = METHOD_PASSWORD

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
    assert not RoleBinding.objects.filter(user=target, role=role).exists()


def test_non_sensitive_mutation_unaffected_by_missing_elevation(permission_resolver):
    """create_team has no @requires_elevation gate — running it
    against an un-elevated session must succeed."""
    org = Organization.objects.create(name="Acme", slug="acme")
    admin = _admin_user(password="pw")
    permission_resolver.grant(Permission.TEAM_CREATE)
    session = _FakeSession()  # un-elevated

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().create_team(
            _info(admin, session=session),
            input=CreateTeamInput(
                organization_id=org.guid,
                name="Eng",
                slug="eng",
            ),
        )

    assert result.ok is True, result.errors
    assert Team.objects.filter(slug="eng", organization=org).exists()


def test_decorator_marks_resolver_with_introspection_attribute():
    """list_gated_resolvers walks the live schema looking for the
    ``__astrolift_step_up_required__`` marker. Confirm the decorator
    sets it on the wrapped function so introspection works."""

    @requires_elevation(action_label="test.thing")
    def _resolver(self, info, x: int) -> MutationResult:  # type: ignore[no-redef]
        return MutationResult.success(x)

    assert getattr(_resolver, "__astrolift_step_up_required__", False) is True
    assert getattr(_resolver, "__astrolift_step_up_label__", None) == "test.thing"


# ---- bulk mutation path -----------------------------------------------


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_bulk_revoke_blocked_when_unelevated(permission_resolver):
    """Bulk mutations get the same gate — a sibling test of the
    single-row path so a future refactor that drops the decorator on
    one side gets caught."""
    org = Organization.objects.create(name="Acme", slug="acme")
    admin = _admin_user(password="pw")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    session = _FakeSession()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().bulk_revoke_astrolift_role_bindings(
            _info(admin, session=session),
            input=BulkRevokeRoleBindingsInput(binding_ids=["00000000-0000-0000-0000-000000000000"]),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.STEP_UP_REQUIRED.value


# ---- query: astroliftElevationStatus ----------------------------------


def test_elevation_status_query_reflects_elevation():
    user = _admin_user(password="pw")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=120)

    result = IdentityQuery().astrolift_elevation_status(_info(user, session=session))

    assert result.elevated is True
    assert result.method == METHOD_PASSWORD
    assert result.seconds_remaining > 0
    # required_for is populated from the live schema; we only assert
    # the shape (it's a list of strings), not the contents.
    assert isinstance(result.required_for, list)


def test_elevation_status_query_for_unauthenticated_returns_unelevated():
    request = SimpleNamespace(user=None, session=None)
    info = SimpleNamespace(context=SimpleNamespace(request=request, user=None))

    result = IdentityQuery().astrolift_elevation_status(info)

    assert result.elevated is False
    assert result.elevated_until is None
    assert result.method is None
    assert result.required_for == []


# ---- credential verifier sanity ---------------------------------------


def test_default_password_verifier_rejects_unusable_password_users():
    """SSO-only users (created with set_unusable_password) can't
    elevate via the password path."""
    from astrolift_identity.session_elevation import default_password_verifier

    user = _admin_user()  # no password set
    user.set_unusable_password()
    user.save(update_fields=["password"])

    assert default_password_verifier(user, METHOD_PASSWORD, "anything") is False


def test_known_methods_includes_spec_27_set():
    # Spec 27 §4.1 lists these as the credential carriers.
    assert {"password", "otp", "webauthn", "magic_link"} <= KNOWN_METHODS


def test_compose_verifiers_returns_first_truthy():
    from astrolift_identity.session_elevation import compose_verifiers

    def yes_for_webauthn(_user, method, _cred):
        return method == METHOD_WEBAUTHN

    def yes_for_password(_user, method, _cred):
        return method == METHOD_PASSWORD

    combined = compose_verifiers(yes_for_webauthn, yes_for_password)
    fake_user = SimpleNamespace(is_active=True)
    assert combined(fake_user, METHOD_WEBAUTHN, "x") is True
    assert combined(fake_user, METHOD_PASSWORD, "x") is True
    assert combined(fake_user, "magic_link", "x") is False
