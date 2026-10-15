"""Per-user step-up PIN (#2399): hashing, org opt-in, lockout, set gating."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password

from astrolift_identity.models import Member, Organization, StepUpPin
from astrolift_identity.schema.mutations import (
    ElevateAdminSessionInput,
    IdentityMutation,
    SetStepUpPinInput,
)
from astrolift_identity.schema.mutations.types import UpdateOrganizationInput
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_identity.session_elevation import (
    KNOWN_METHODS,
    METHOD_PASSWORD,
    METHOD_PIN,
    elevate,
    get_status,
    is_elevated,
    reset_credential_verifier_for_tests,
)
from astrolift_identity.step_up import _supported_step_up_methods
from astrolift_identity.step_up_pin import MAX_FAILED_ATTEMPTS, StepUpPinError, set_pin
from core.mutations import AuditEntry, ErrorCode, register_audit_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()
PIN = "482913"


class _FakeSession(dict):
    modified = False


@pytest.fixture(autouse=True)
def _production_verifiers():
    """The verifiers the app registers at startup, PIN included."""
    apps.get_app_config("astrolift_identity").ready()
    yield
    reset_credential_verifier_for_tests()
    apps.get_app_config("astrolift_identity").ready()


@pytest.fixture
def audit_capture():
    captured: list[AuditEntry] = []
    from core.mutations import _audit_writer as _orig_writer  # noqa: PLC2701

    register_audit_writer(captured.append)
    yield captured
    register_audit_writer(_orig_writer)


def _org(*, allow: bool) -> Organization:
    slug = f"pin-{uuid.uuid4().hex[:8]}"
    return Organization.objects.create(name=slug, slug=slug, allow_step_up_pin=allow)


def _member(org: Organization) -> User:
    name = f"pin-{uuid.uuid4().hex[:8]}"
    user = User.objects.create(username=name, email=f"{name}@astrolift.dev")
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


def _info(user, session):
    request = SimpleNamespace(user=user, session=session)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _in(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


def _elevate_with_pin(org, user, session, pin=PIN):
    with _in(org, user):
        return IdentityMutation().elevate_admin_session(
            _info(user, session), input=ElevateAdminSessionInput(method=METHOD_PIN, credential=pin)
        )


# ---- storage ------------------------------------------------------------


def test_pin_is_stored_as_a_django_password_hash():
    user = _member(_org(allow=True))
    assert set_pin(user, PIN) is False
    row = StepUpPin.objects.get(user=user)
    assert PIN not in row.pin_hash
    assert check_password(PIN, row.pin_hash)
    assert set_pin(user, "12345678") is True, "a second set is a change"


@pytest.mark.parametrize("bad", ["12345", "123456789", "12a456", "", " 123456"])
def test_pin_must_be_six_to_eight_digits(bad):
    user = _member(_org(allow=True))
    with pytest.raises(StepUpPinError):
        set_pin(user, bad)
    assert not StepUpPin.objects.filter(user=user).exists()


def test_pin_is_a_known_method():
    assert METHOD_PIN in KNOWN_METHODS


# ---- org gate -----------------------------------------------------------


def test_org_setting_is_off_by_default():
    org = Organization.objects.create(name="Default", slug=f"pin-default-{uuid.uuid4().hex[:6]}")
    assert org.allow_step_up_pin is False


def test_pin_is_never_accepted_when_the_org_disallows_it(audit_capture):
    org = _org(allow=False)
    user = _member(org)
    set_pin(user, PIN)
    session = _FakeSession()

    result = _elevate_with_pin(org, user, session)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert not is_elevated(session)
    assert StepUpPin.objects.get(user=user).failed_attempts == 0, "a refused org must not burn attempts"


def test_pin_elevates_when_the_org_allows_it():
    org = _org(allow=True)
    user = _member(org)
    set_pin(user, PIN)
    session = _FakeSession()

    result = _elevate_with_pin(org, user, session)

    assert result.ok is True, result.errors
    with _in(org, user):
        status = get_status(session)
    assert status.elevated and status.method == METHOD_PIN


def test_pin_is_refused_under_an_org_the_person_does_not_belong_to():
    home = _org(allow=False)
    other = _org(allow=True)
    user = _member(home)
    set_pin(user, PIN)
    session = _FakeSession()

    assert _elevate_with_pin(other, user, session).ok is False
    assert not session


def test_a_pin_elevation_does_not_count_where_pins_are_not_allowed():
    allowing = _org(allow=True)
    forbidding = _org(allow=False)
    user = _member(allowing)
    session = _FakeSession()
    elevate(session, method=METHOD_PIN, ttl_seconds=300)

    with _in(allowing, user):
        assert get_status(session).elevated
    with _in(forbidding, user):
        assert not get_status(session).elevated
    allowing.allow_step_up_pin = False
    allowing.save(update_fields=["allow_step_up_pin"])
    with _in(allowing, user):
        assert not get_status(session).elevated, "turning the setting off ends PIN elevations"
    with _in(forbidding, user):
        elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)
        assert get_status(session).elevated, "other methods are unaffected"


# ---- lockout ------------------------------------------------------------


def test_lockout_after_max_wrong_pins_then_the_right_pin_is_refused(audit_capture):
    org = _org(allow=True)
    user = _member(org)
    set_pin(user, PIN)
    session = _FakeSession()

    for _ in range(MAX_FAILED_ATTEMPTS):
        assert _elevate_with_pin(org, user, session, pin="000000").ok is False
    row = StepUpPin.objects.get(user=user)
    assert row.locked_at is not None
    assert "auth.step_up_pin.locked" in [e.action for e in audit_capture]

    assert _elevate_with_pin(org, user, session).ok is False
    assert not session
    with _in(org, user):
        assert METHOD_PIN not in _supported_step_up_methods(session, user=user), "locked falls back"

    set_pin(user, "13572468")
    assert _elevate_with_pin(org, user, session, pin="13572468").ok is True, "a new PIN unlocks"


def test_a_right_pin_resets_the_count_and_malformed_ones_do_not_count():
    org = _org(allow=True)
    user = _member(org)
    set_pin(user, PIN)
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        _elevate_with_pin(org, user, _FakeSession(), pin="000000")
    for _ in range(MAX_FAILED_ATTEMPTS):
        _elevate_with_pin(org, user, _FakeSession(), pin="12")
    assert StepUpPin.objects.get(user=user).failed_attempts == MAX_FAILED_ATTEMPTS - 1

    assert _elevate_with_pin(org, user, _FakeSession()).ok is True
    row = StepUpPin.objects.get(user=user)
    assert row.failed_attempts == 0 and row.locked_at is None


# ---- supported methods --------------------------------------------------


def test_status_offers_the_pin_first_only_when_allowed_and_set():
    allowing = _org(allow=True)
    forbidding = _org(allow=False)
    user = _member(allowing)
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=forbidding.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    session = _FakeSession({"login_method": "sso"})

    def methods(org):
        with _in(org, user):
            return IdentityQuery().astrolift_elevation_status(_info(user, session)).supported_methods

    assert methods(allowing) == ["sso"], "no PIN set yet"
    set_pin(user, PIN)
    assert methods(allowing) == ["pin", "sso"]
    assert methods(forbidding) == ["sso"]


# ---- set / change / clear -----------------------------------------------


def _set(org, user, session, pin=PIN):
    with _in(org, user):
        return IdentityMutation().set_step_up_pin(_info(user, session), input=SetStepUpPinInput(pin=pin))


def test_setting_a_pin_requires_a_fresh_sso_or_password_elevation(audit_capture):
    org = _org(allow=True)
    user = _member(org)
    session = _FakeSession({"login_method": "sso"})

    refused = _set(org, user, session)
    assert refused.ok is False
    assert refused.errors[0].code == ErrorCode.STEP_UP_REQUIRED.value
    assert not StepUpPin.objects.filter(user=user).exists()

    elevate(session, method="sso", ttl_seconds=300)
    first = _set(org, user, session)
    assert first.ok is True, first.errors
    assert first.data.replaced is False and first.data.status.is_set

    # A PIN elevation cannot vouch for the next PIN, and the refusal
    # asks for the login method, not the PIN.
    pin_session = _FakeSession({"login_method": "sso"})
    elevate(pin_session, method=METHOD_PIN, ttl_seconds=300)
    with_pin = _set(org, user, pin_session, pin="11223344")
    assert with_pin.ok is False
    assert with_pin.errors[0].code == ErrorCode.STEP_UP_REQUIRED.value
    assert with_pin.errors[0].supported_methods == ["sso"]

    changed = _set(org, user, session, pin="11223344")
    assert changed.ok is True and changed.data.replaced is True
    assert check_password("11223344", StepUpPin.objects.get(user=user).pin_hash)

    rows = [
        (e.action, e.extra)
        for e in audit_capture
        if e.action == "auth.step_up_pin.set" and e.decision == "ALLOW"
    ]
    assert [extra for _a, extra in rows if extra] == [{"replaced": False}, {"replaced": True}]


def test_setting_a_pin_is_refused_when_the_org_disallows_it():
    org = _org(allow=False)
    user = _member(org)
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    result = _set(org, user, session)
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert not StepUpPin.objects.filter(user=user).exists()


def test_clearing_a_pin_removes_it_and_is_audited(audit_capture):
    org = _org(allow=True)
    user = _member(org)
    set_pin(user, PIN)

    with _in(org, user):
        result = IdentityMutation().clear_step_up_pin(_info(user, _FakeSession()))
    assert result.ok is True
    assert result.data.is_set is False
    assert not StepUpPin.objects.filter(user=user).exists()
    assert "auth.step_up_pin.clear" in [e.action for e in audit_capture]


def test_my_step_up_pin_reports_the_state():
    org = _org(allow=True)
    user = _member(org)
    with _in(org, user):
        before = IdentityQuery().my_step_up_pin(_info(user, _FakeSession()))
        set_pin(user, PIN)
        after = IdentityQuery().my_step_up_pin(_info(user, _FakeSession()))
    assert (before.org_allows, before.is_set, before.locked) == (True, False, False)
    assert (after.is_set, after.locked) == (True, False)


# ---- org setting --------------------------------------------------------


def test_changing_the_org_setting_is_audited(audit_capture, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = _org(allow=False)
    user = _member(org)

    def update(**fields):
        with _in(org, user):
            return IdentityMutation().update_organization(
                _info(user, _FakeSession()), UpdateOrganizationInput(id=str(org.guid), **fields)
            )

    assert update(allow_step_up_pin=True).data.allow_step_up_pin is True
    assert update(allow_step_up_pin=True).ok, "a no-op write"
    assert update(name="Renamed").ok
    assert update(allow_step_up_pin=False).data.allow_step_up_pin is False

    changes = [e.extra for e in audit_capture if e.action == "org.step_up_pin_policy.changed"]
    assert changes == [{"allow_step_up_pin": True}, {"allow_step_up_pin": False}]
