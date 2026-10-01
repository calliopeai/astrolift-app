"""Real PostgreSQL/API erasure, exact attribution and append-only boundaries."""

import json
import threading
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import Client, RequestFactory
from django.utils import timezone

from astrolift_identity.anonymization_state import is_anonymized_user
from astrolift_identity.models import ApiToken, AstroliftSession, Member, RoleBinding
from astrolift_identity.personal_history import redact_personal_history
from astrolift_identity.schema.types import UserType, user_to_type
from astrolift_identity.tests import test_anonymize_tenancy_1979 as support
from astrolift_operations.models import AuditEvent, Event, NotificationDelivery
from config.schema import schema
from core.schema.audit import MutationAuditLog
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
stock = support.stock
no_external_side_effects = support._no_external_side_effects

QUERY = """mutation Erase($id: GUID!) {
  astroliftAnonymizeUser(input: {userGid: $id}) {
    ok errors { code message } data { anonymizedUserId wasSelf requiresLogout lifecycle }
  }
}"""


def api(org, actor, target):
    context = SimpleNamespace(
        user=actor, request=SimpleNamespace(user=actor, META={"REMOTE_ADDR": "192.0.2.19"})
    )
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=actor.pk)):
        result = schema.execute_sync(QUERY, variable_values={"id": str(target.pk)}, context_value=context)
    assert not result.errors, "anonymization GraphQL execution failed"
    return result.data["astroliftAnonymizeUser"]


def snapshot(row):
    return type(row).objects.values().get(pk=row.pk)


def stored_anonymous(user):
    get_user_model().objects.filter(pk=user.pk).update(
        is_active=False, email=f"anon-{user.pk}@anon-astrolift.net"
    )
    user.refresh_from_db()


def remote_claims(user, *, sub="owned-idp-sub", issuer="https://issuer.test", email=None, verified=True):
    return {
        "id_token": "controlled-unsaved-test-token",
        "userinfo": {
            "sub": sub,
            "iss": issuer,
            "email": user.email if email is None else email,
            "email_verified": verified,
            "given_name": "Returned Given",
            "family_name": "Returned Family",
            "nickname": "Returned Nick",
            "name": "Returned Name",
            "picture": "https://avatar.test/returned",
            "locale": "en",
            "updated_at": timezone.now(),
            "aud": "controlled",
            "iat": 1,
            "exp": 2,
            "sid": "opaque-session",
            "nonce": "opaque-nonce",
        },
    }


def callback_request():
    request = RequestFactory().get("/app/auth1/callback")
    SessionMiddleware(lambda request: None).process_request(request)
    request.user = AnonymousUser()
    return request


@pytest.mark.parametrize("state", ["anonymous", "disabled_ordinary"])
def test_actual_callback_cannot_restore_linked_inactive_identity_or_issue_session(stock, monkeypatch, state):
    from auth1.models import UserInfo
    from auth1.sessions import Auth1SessionWorkflow
    from core.models import Profile

    org = support._org("callback")
    user = support._member(org, support._user("callback"))
    support._bind(user, stock["org_owner"], "ORG", org.pk)
    claims = remote_claims(user)
    UserInfo.objects.create(internal_user=user, **claims["userinfo"])
    if state == "anonymous":
        Profile.anonymize_user(user)
    else:
        get_user_model().objects.filter(pk=user.pk).update(is_active=False)
    before_user = get_user_model().objects.values().get(pk=user.pk)
    before_cache = UserInfo.objects.values().get(pk=claims["userinfo"]["sub"])
    before_members, before_grants = list(Member.objects.values()), list(RoleBinding.objects.values())
    count = get_user_model().objects.count()
    monkeypatch.setenv("ASTROLIFT_AUTO_SIGNUP_DOMAINS", claims["userinfo"]["email"].rsplit("@", 1)[1])
    monkeypatch.setattr(
        Auth1SessionWorkflow,
        "_client",
        SimpleNamespace(auth0=SimpleNamespace(authorize_access_token=lambda request: claims)),
    )
    request = callback_request()
    with pytest.raises(PermissionDenied, match="Account is inactive"):
        Auth1SessionWorkflow.callback(request)
    assert "_auth_user_id" not in request.session
    assert get_user_model().objects.count() == count
    assert get_user_model().objects.values().get(pk=user.pk) == before_user
    assert UserInfo.objects.values().get(pk=claims["userinfo"]["sub"]) == before_cache
    assert list(Member.objects.values()) == before_members
    assert list(RoleBinding.objects.values()) == before_grants


@pytest.mark.parametrize("issuer", ["https://foreign-issuer.test", ""])
def test_actual_idp_registration_refuses_changed_or_unknown_existing_issuer(stock, issuer):
    from auth1.models import UserInfo
    from auth1.sessions import Auth1SessionWorkflow

    user = support._user("issuer")
    original = remote_claims(user)
    UserInfo.objects.create(internal_user=user, **original["userinfo"])
    before = UserInfo.objects.values().get(pk=original["userinfo"]["sub"])
    request = callback_request()
    with pytest.raises(PermissionDenied, match="issuer does not match"):
        Auth1SessionWorkflow._register_remote_user(request, remote_claims(user, issuer=issuer))
    assert UserInfo.objects.values().get(pk=original["userinfo"]["sub"]) == before
    assert "_auth_user_id" not in request.session


def test_active_existing_idp_subject_keeps_account_when_verified_email_changes(stock):
    from auth1.models import UserInfo
    from auth1.sessions import Auth1SessionWorkflow

    user, other = support._user("existing"), support._user("other-email")
    original = remote_claims(user)
    UserInfo.objects.create(internal_user=user, **original["userinfo"])
    request = callback_request()
    result = Auth1SessionWorkflow._register_remote_user(request, remote_claims(user, email=other.email))
    assert result.userinfo.internal_user_id == user.pk
    assert request.session["_auth_user_id"] == str(user.pk)
    assert UserInfo.objects.get(pk=original["userinfo"]["sub"]).internal_user_id == user.pk
    user.refresh_from_db()
    assert user.email == original["userinfo"]["email"] and user.is_active


@pytest.mark.parametrize("provision", [False, True])
def test_verified_first_idp_registration_keeps_existing_and_new_user_flows(stock, monkeypatch, provision):
    from auth1.models import UserInfo
    from auth1.sessions import Auth1SessionWorkflow

    existing = support._user("active-email")
    email = "new-user@controlled-signup.test" if provision else existing.email
    before_count = get_user_model().objects.count()
    monkeypatch.setenv("ASTROLIFT_AUTO_SIGNUP_DOMAINS", "controlled-signup.test")
    request = callback_request()
    # Keep the released Cognito verified-email/updated-at fallbacks.
    claims = remote_claims(
        existing, email=email, issuer="https://cognito-idp.controlled.test/pool", verified=False
    )
    claims["userinfo"].pop("updated_at")
    result = Auth1SessionWorkflow._register_remote_user(request, claims)
    assert result.userinfo.email_verified and result.userinfo.updated_at
    account = result.userinfo.internal_user
    assert account.is_active and request.session["_auth_user_id"] == str(account.pk)
    assert UserInfo.objects.get(pk=claims["userinfo"]["sub"]).internal_user_id == account.pk
    assert get_user_model().objects.count() == before_count + int(provision)
    assert (account.pk != existing.pk) is provision


def test_inactive_email_lookup_and_unverified_new_claims_precede_all_writes(stock, monkeypatch):
    from auth1.models import UserInfo
    from auth1.sessions import Auth1SessionWorkflow, EmailNotVerifiedException

    user = support._user("disabled-email", is_active=False)
    count = get_user_model().objects.count()
    request = callback_request()
    with pytest.raises(PermissionDenied, match="Account is inactive"):
        Auth1SessionWorkflow._register_remote_user(request, remote_claims(user))
    monkeypatch.setenv("ASTROLIFT_AUTO_SIGNUP_DOMAINS", "new-idp.test")
    with pytest.raises(EmailNotVerifiedException), transaction.atomic():
        Auth1SessionWorkflow._register_remote_user(
            request, remote_claims(user, email="unverified@new-idp.test", verified=False)
        )
    assert get_user_model().objects.count() == count and not UserInfo.objects.exists()
    assert "_auth_user_id" not in request.session


@pytest.mark.django_db(transaction=True)
def test_idp_callback_waits_for_account_erasure_lock_and_cannot_restore_cache(stock):
    from auth1.models import UserInfo
    from auth1.sessions import Auth1SessionWorkflow
    from core.models import Profile

    user = support._user("race")
    original_user_count = get_user_model().objects.count()
    claims = remote_claims(user)
    UserInfo.objects.create(internal_user=user, **claims["userinfo"])
    locked, release, attempting, finished = (threading.Event() for _ in range(4))
    outcomes = []

    def erase():
        close_old_connections()
        try:
            with transaction.atomic():
                owned = get_user_model().objects.select_for_update().get(pk=user.pk)
                Profile.anonymize_user(owned)
                locked.set()
                assert release.wait(10), "controlled erasure release timed out"
            outcomes.append("erased")
        finally:
            close_old_connections()

    def register():
        close_old_connections()
        request = callback_request()

        def record(execute, sql, params, many, context):
            if 'FROM "auth_user"' in sql and "FOR UPDATE" in sql:
                attempting.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(record):
                Auth1SessionWorkflow._register_remote_user(request, claims)
        except PermissionDenied:
            outcomes.append("inactive_refused")
        finally:
            outcomes.append("no_session" if "_auth_user_id" not in request.session else "unexpected_session")
            finished.set()
            close_old_connections()

    eraser, callback = threading.Thread(target=erase), threading.Thread(target=register)
    eraser.start()
    try:
        assert locked.wait(10), "controlled erasure lock timed out"
        callback.start()
        assert attempting.wait(10), "callback did not reach user admission lock"
        assert not finished.wait(0.1), "callback bypassed the held user lock"
    finally:
        release.set()
        eraser.join(10)
        if callback.ident is not None:
            callback.join(10)
    assert not eraser.is_alive() and not callback.is_alive()
    assert set(outcomes) == {"erased", "inactive_refused", "no_session"}
    cache = UserInfo.objects.get(pk=claims["userinfo"]["sub"])
    assert cache.internal_user_id == user.pk and cache.email == f"anon-{user.pk}@anon-astrolift.net"
    assert not cache.name and get_user_model().objects.count() == original_user_count


@pytest.mark.parametrize(
    "active,email,expected",
    [
        (True, "ordinary@example.test", False),
        (False, "ordinary@example.test", False),
        (True, "anon-marker@anon-astrolift.net", False),
        (False, "anon-marker@anon-astrolift.net", True),
        (False, "anon-marker@ANON-ASTROLIFT.NET", False),
        (False, "anon-marker@anon-astrolift.net.example.org", False),
    ],
)
def test_nullable_read_state_matches_actual_database_privacy_admission(stock, active, email, expected):
    user = support._user("state", is_active=active)
    get_user_model().objects.filter(pk=user.pk).update(email=email)
    user.refresh_from_db()
    assert is_anonymized_user(user) is expected
    assert user_to_type(user).is_anonymized is expected
    assert UserType(id=str(user.pk), username="unknown", email="", is_active=False).is_anonymized is None
    if expected:
        assert redact_personal_history(user)["audit_events"] == 0
    else:
        with pytest.raises(DatabaseError, match="privacy target unavailable"), transaction.atomic():
            redact_personal_history(user)


def test_anonymous_read_field_keeps_member_permission_and_tenant_boundaries(stock):
    org, foreign_org = support._org("read"), support._org("foreign")
    manager = support._member_manager(org)
    ordinary = support._member(org, support._user("ordinary", is_active=False), active=False)
    anonymous = support._member(org, support._user("anonymous"), active=False)
    foreign = support._member(foreign_org, support._user("foreign"), active=False)
    stored_anonymous(anonymous)
    stored_anonymous(foreign)
    query = "query { astroliftMembers { user { id isActive isAnonymized } } }"

    def read(actor):
        with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=actor.pk)):
            return schema.execute_sync(
                query, context_value=SimpleNamespace(user=actor, request=SimpleNamespace(user=actor))
            )

    response = read(manager)
    assert not response.errors, "member read execution failed"
    rows = {row["user"]["id"]: row["user"] for row in response.data["astroliftMembers"]}
    assert rows[str(anonymous.pk)] == {"id": str(anonymous.pk), "isActive": False, "isAnonymized": True}
    assert rows[str(ordinary.pk)] == {"id": str(ordinary.pk), "isActive": False, "isAnonymized": False}
    assert str(foreign.pk) not in rows
    unprivileged = support._member(org, support._user("unprivileged"))
    denied = read(unprivileged)
    assert denied.errors and not (denied.data or {}).get("astroliftMembers")


def unchanged_structure(row, old, *, privacy_fields):
    current = snapshot(row)
    assert {key: value for key, value in current.items() if key not in privacy_fields} == {
        key: value for key, value in old.items() if key not in privacy_fields
    }


def audit(org, actor, target, *, data=None, **extra):
    return AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id=str(actor.pk),
        action="historical.identity",
        decision="ALLOW",
        target_kind="user",
        target_id=str(target.pk),
        data=data or {},
        **extra,
    )


def test_graphql_reduces_target_pii_preserves_mixed_subjects_and_structural_history(stock):
    org = support._org("history")
    target = support._member(org, support._user("erased", first_name="Unique Target Name"))
    other = support._member(org, support._user("retained", first_name="Other Person"))
    old_email = target.email
    actor_row = audit(
        org,
        target,
        other,
        actor_display=target.first_name,
        request_ip="192.0.2.11",
        request_user_agent="TargetBrowser",
        target_slug=other.username,
        data={
            "actor_email": old_email,
            "email": other.email,
            "request_email": old_email,
            "foreign": {"user_id": other.pk, "email": other.email},
            "unknown": {"email": "unattributed@example.test"},
        },
        reasoning=[
            {"actor": {"id": target.pk, "name": "Former Target Name"}},
            {"subject": {"id": other.pk, "email": other.email}},
        ],
    )
    subject_row = audit(
        org,
        other,
        target,
        actor_display=other.first_name,
        request_ip="192.0.2.22",
        request_user_agent="OtherBrowser",
        target_slug=target.username,
        data={
            "email": "former-target@example.test",
            "first_name": "Former Name",
            "actor_email": other.email,
            "subject": {"id": target.pk, "email": old_email},
            "recipient": {"user_id": other.pk, "email": other.email},
        },
    )
    foreign_row = audit(org, other, other, data={"email": other.email}, actor_display=other.first_name)
    collision = AuditEvent.objects.create(
        organization=org,
        actor_kind="api_token",
        actor_id=str(target.pk),
        action="collision",
        decision="ALLOW",
        actor_display="Other token",
        request_ip="192.0.2.33",
        data={"actor_email": other.email, "email": other.email},
    )
    event = Event.objects.create(
        organization=org,
        actor_user=other,
        resource_kind="user",
        resource_id=str(target.pk),
        event_type="user.changed",
        payload={
            "email": old_email,
            "profile": {"name": "Old Full Name", "birth_date": "1990-01-01"},
            "members": [
                {"user_id": target.pk, "email": old_email},
                {"user_id": other.pk, "email": other.email},
            ],
            "foreign": {"user_id": other.pk, "email": other.email},
            "unknown": {"email": other.email},
            "actor_email": other.email,
        },
    )
    session_event = Event.objects.create(
        organization=org,
        actor_user=target,
        resource_kind="astrolift_session",
        resource_id="opaque-session-guid",
        event_type="auth.session.created",
        payload={
            "user_id": target.pk,
            "ip_address": "192.0.2.11",
            "user_agent": "TargetBrowser",
            "device_label": "Personal laptop",
            "geo_hint": "Local city",
            "session_pk": 14,
            "session_guid": "opaque-session-guid",
            "client_kind": "web",
            "created_at": "2026-09-01",
        },
    )
    rows = [actor_row, subject_row, foreign_row, collision, event, session_event]
    old = {row.pk: snapshot(row) for row in (actor_row, subject_row, foreign_row, collision)}
    old_events = {row.pk: snapshot(row) for row in (event, session_event)}
    result = api(org, target, target)
    assert result["ok"] and result["data"]["requiresLogout"] is True
    assert result["data"]["lifecycle"] == "deactivated"
    actor_row.refresh_from_db()
    subject_row.refresh_from_db()
    event.refresh_from_db()
    session_event.refresh_from_db()
    assert actor_row.actor_display == actor_row.request_ip == actor_row.request_user_agent == "[anonymized]"
    assert actor_row.target_slug == other.username
    assert actor_row.data == {
        **old[actor_row.pk]["data"],
        "actor_email": "[anonymized]",
        "request_email": "[anonymized]",
    }
    assert actor_row.reasoning == [
        {"actor": {"id": target.pk, "name": "[anonymized]"}},
        {"subject": {"id": other.pk, "email": other.email}},
    ]
    assert subject_row.actor_display == other.first_name and subject_row.request_ip == "192.0.2.22"
    assert subject_row.target_slug == "[anonymized]"
    assert subject_row.data == {
        **old[subject_row.pk]["data"],
        "email": "[anonymized]",
        "first_name": "[anonymized]",
        "subject": {"id": target.pk, "email": "[anonymized]"},
    }
    assert snapshot(foreign_row) == old[foreign_row.pk] and snapshot(collision) == old[collision.pk]
    assert event.payload == {
        **old_events[event.pk]["payload"],
        "email": "[anonymized]",
        "profile": {"name": "[anonymized]", "birth_date": "[anonymized]"},
        "members": [
            {"user_id": target.pk, "email": "[anonymized]"},
            {"user_id": other.pk, "email": other.email},
        ],
    }
    assert session_event.payload == {
        **old_events[session_event.pk]["payload"],
        **dict.fromkeys(("ip_address", "user_agent", "device_label", "geo_hint"), "[anonymized]"),
    }
    for row in rows:
        unchanged_structure(
            row,
            (old if isinstance(row, AuditEvent) else old_events)[row.pk],
            privacy_fields={
                "data",
                "reasoning",
                "actor_display",
                "request_ip",
                "request_user_agent",
                "target_slug",
            }
            if isinstance(row, AuditEvent)
            else {"payload"},
        )
    assert MutationAuditLog.objects.filter(user=target, operation="identity.user.anonymized").exists()
    assert not MutationAuditLog.objects.filter(user=target, ip_address__isnull=False).exists()
    completed = AuditEvent.objects.get(action="identity.user.anonymized")
    assert completed.actor_id == completed.target_id == str(target.pk)
    assert completed.decision == "ALLOW"
    assert old_email not in json.dumps(snapshot(completed), default=str)
    assert "Unique Target Name" not in json.dumps(snapshot(completed), default=str)
    target.refresh_from_db()
    assert not target.is_active and target.email.endswith("@anon-astrolift.net")
    assert get_user_model().objects.get(pk=other.pk).email == other.email


@pytest.mark.parametrize(
    "payload",
    [
        {"unknown": {"email": "unknown@example.test"}},
        {"subject": {"id": "not-a-user", "email": "unknown@example.test"}},
        {"subject": {"id": "9223372036854775808", "email": "unknown@example.test"}},
        {"user_id": "unknown", "email": "unknown@example.test"},
        {"user": {"id": 991, "user_id": 992, "email": "ambiguous@example.test"}},
        {"email": {"user_id": 991, "email": "foreign@example.test"}},
    ],
)
def test_unknown_ambiguous_and_structured_foreign_payloads_are_excluded(stock, payload):
    org = support._org("excluded")
    target = support._member(org, support._user("subject"))
    row = Event.objects.create(
        organization=org,
        resource_kind="user",
        resource_id=str(target.pk),
        event_type="user.changed",
        payload=payload,
    )
    before = snapshot(row)
    assert api(org, target, target)["ok"]
    assert snapshot(row) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "DENY"),
        ("data", {"email": "forged@example.test"}),
        ("actor_display", "forged name"),
        ("target_id", "999"),
    ],
)
def test_forged_settable_gate_cannot_change_structural_or_foreign_pii(stock, field, value):
    org = support._org("forged")
    target, other = support._user("target"), support._user("foreign")
    stored_anonymous(target)
    row = audit(org, other, other, data={"email": other.email}, actor_display=other.username)
    before = snapshot(row)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('astrolift.privacy_user', %s, true)", [str(target.pk)])
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
            AuditEvent.objects.filter(pk=row.pk).update(**{field: value})
        cursor.execute("SELECT set_config('astrolift.privacy_user', '', true)")
    assert snapshot(row) == before


@pytest.mark.parametrize("state", ["active", "disabled_ordinary", "missing", "lookalike"])
def test_forged_gate_refuses_even_exact_projection_without_anonymous_transition(stock, state):
    org = support._org("state")
    target = support._user("target")
    if state in ("disabled_ordinary", "lookalike"):
        target.is_active = False
        if state == "lookalike":
            target.email = "ordinary@anon-astrolift.net.example.org"
        target.save()
    erased = target.pk if state != "missing" else 99999999
    row = AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id=str(erased),
        target_kind="user",
        target_id=str(erased),
        action="state.guard",
        decision="ALLOW",
        data={"email": "old-subject@example.test"},
    )
    before = snapshot(row)
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('astrolift.privacy_user', %s, true)", [str(erased)])
        try:
            with pytest.raises(DatabaseError, match="privacy target unavailable"), transaction.atomic():
                AuditEvent.objects.filter(pk=row.pk).update(data={"email": "[anonymized]"})
        finally:
            cursor.execute("SELECT set_config('astrolift.privacy_user', '', true)")
        with pytest.raises(DatabaseError, match="privacy target unavailable"), transaction.atomic():
            cursor.execute("SELECT astrolift_redact_user_history(%s)", [erased])
    assert snapshot(row) == before


def test_history_failure_rolls_back_account_profile_and_every_prior_projection(stock):
    org = support._org("rollback")
    target = support._member(org, support._user("target", first_name="Retained on failure"))
    row = audit(org, target, target, data={"email": target.email})
    event = Event.objects.create(
        organization=org,
        actor_user=target,
        resource_kind="user",
        resource_id=str(target.pk),
        event_type="user.changed",
        payload={"email": target.email},
    )
    before_user = get_user_model().objects.values().get(pk=target.pk)
    before_profile = target.profile.__class__.objects.values().get(pk=target.profile.pk)
    before_audit, before_event = snapshot(row), snapshot(event)
    memberships = list(Member.objects.filter(user=target).values())
    with connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION pg_temp.test_privacy_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'controlled owned privacy failure'; RETURN NEW;
            END; $$;""")
        cursor.execute("""CREATE TRIGGER zz_owned_privacy_failure BEFORE UPDATE
            ON astrolift_operations_event FOR EACH ROW
            EXECUTE FUNCTION pg_temp.test_privacy_failure();""")
    try:
        response = api(org, target, target)
        assert response["ok"] is False
        assert get_user_model().objects.values().get(pk=target.pk) == before_user
        assert target.profile.__class__.objects.values().get(pk=target.profile.pk) == before_profile
        assert snapshot(row) == before_audit and snapshot(event) == before_event
        assert list(Member.objects.filter(user=target).values()) == memberships
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('astrolift.privacy_user', true)")
            assert cursor.fetchone()[0] in (None, "")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER zz_owned_privacy_failure ON astrolift_operations_event")
            cursor.execute("DROP FUNCTION pg_temp.test_privacy_failure()")


def test_projection_is_idempotent_and_gate_closes_after_success_and_exception(stock):
    org = support._org("gate")
    target = support._user("target")
    row = audit(org, target, target, data={"email": target.email})
    stored_anonymous(target)
    assert redact_personal_history(target)["audit_events"] == 1
    clean = snapshot(row)
    assert redact_personal_history(target)["audit_events"] == 0
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('astrolift.privacy_user', true)")
        assert cursor.fetchone()[0] in (None, "")
        with pytest.raises(DatabaseError), transaction.atomic():
            cursor.execute("SELECT astrolift_redact_user_history(%s)", [-1])
        cursor.execute("SELECT current_setting('astrolift.privacy_user', true)")
        assert cursor.fetchone()[0] in (None, "")
    assert snapshot(row) == clean
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        AuditEvent.objects.filter(pk=row.pk).update(decision="DENY")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        AuditEvent.objects.filter(pk=row.pk).delete()


def test_repeat_anonymous_user_repairs_new_attributed_history_without_identity_churn(stock):
    org = support._org("repeat")
    operator = support._user("operator", is_superuser=True)
    target = support._member(org, support._user("target"))
    assert api(org, operator, target)["ok"]
    target.refresh_from_db()
    identity = (target.username, target.email, target.profile.updated_at)
    late = audit(org, operator, target, data={"email": "old-imported@example.test"})
    again = api(org, operator, target)
    assert again["ok"] and again["data"]["requiresLogout"] is False
    late.refresh_from_db()
    assert late.data == {"email": "[anonymized]"}
    target.refresh_from_db()
    assert (target.username, target.email, target.profile.updated_at) == identity
    before = snapshot(late)
    assert api(org, operator, target)["ok"]
    assert snapshot(late) == before


def test_disabled_lookalike_domain_receives_first_erasure_instead_of_repeat_cleanup(stock):
    org = support._org("lookalike")
    actor = support._member_manager(org)
    target = support._member(org, support._user("lookalike", is_active=False), active=False)
    get_user_model().objects.filter(pk=target.pk).update(email="ordinary@anon-astrolift.net.example.org")
    target.refresh_from_db()
    before_username = target.username
    row = audit(org, actor, target, data={"email": target.email})
    assert not is_anonymized_user(target)
    assert api(org, actor, target)["ok"]
    target.refresh_from_db()
    row.refresh_from_db()
    assert target.username != before_username and target.email.endswith("@anon-astrolift.net")
    assert is_anonymized_user(target) and row.data == {"email": "[anonymized]"}


def test_clears_only_exact_user_owned_caches_including_soft_deleted_rows(stock):
    from auth1.models import UserInfo

    org = support._org("caches")
    target, other = support._user("target"), support._user("other")
    actor_log = MutationAuditLog.objects.create(
        user=target,
        operation="user.changed",
        ip_address="192.0.2.1",
        variables={"input": {"email": other.email}},
        errors=["Unattributed mixed content"],
    )
    foreign_log = MutationAuditLog.objects.create(
        user=other, operation="user.changed", ip_address="192.0.2.2"
    )
    actor_before, foreign_log_before = snapshot(actor_log), snapshot(foreign_log)
    info = UserInfo.objects.create(
        internal_user=target,
        sub="owned-opaque-sub",
        given_name="Target",
        family_name="Family",
        nickname="Nick",
        name="Full Name",
        picture="https://avatar.test/target",
        locale="en",
        updated_at=timezone.now(),
        email=target.email,
        email_verified=True,
        iss="https://identity.test",
        aud="app",
        iat=1,
        exp=2,
        sid="opaque",
        nonce="opaque",
    )
    own = AstroliftSession.objects.create(
        user=target,
        label="Personal laptop",
        last_seen_ip="192.0.2.1",
        last_seen_agent="Browser",
        deleted_at=timezone.now(),
    )
    foreign = AstroliftSession.objects.create(
        user=other, label="Other laptop", last_seen_ip="192.0.2.2", last_seen_agent="Other Browser"
    )
    token = ApiToken.objects.create(
        user=target,
        organization=org,
        name="Retained token",
        token_hash="static-test-hash",
        last_used_ip="192.0.2.1",
        last_used_agent="Browser",
    )
    contact = NotificationDelivery.objects.create(
        user=target,
        organization=org,
        driver_name="test",
        target_kind="email",
        target_address=target.email,
        status="delivered",
    )
    webhook = NotificationDelivery.objects.create(
        user=target,
        organization=org,
        driver_name="test",
        target_kind="webhook",
        target_address="https://org-owned.test/hook",
        status="delivered",
    )
    foreign_before, webhook_before = snapshot(foreign), snapshot(webhook)
    stored_anonymous(target)
    counts = redact_personal_history(target)
    assert counts == {
        "audit_events": 0,
        "events": 0,
        "identity_caches": 1,
        "session_metadata": 1,
        "token_metadata": 1,
        "delivery_contacts": 1,
        "mutation_request_ips": 1,
    }
    info.refresh_from_db()
    own.refresh_from_db()
    token.refresh_from_db()
    contact.refresh_from_db()
    assert info.email == f"anon-{target.pk}@anon-astrolift.net" and not info.email_verified
    assert not any((info.given_name, info.family_name, info.nickname, info.name, info.picture))
    assert info.sub == "owned-opaque-sub" and info.internal_user_id == target.pk
    assert own.last_seen_ip is None and own.last_seen_agent == own.label == "" and own.deleted_at is not None
    assert (
        token.last_used_ip is None and token.last_used_agent == "" and token.token_hash == "static-test-hash"
    )
    assert not token.is_revoked and contact.target_address == ""
    assert snapshot(foreign) == foreign_before and snapshot(webhook) == webhook_before
    assert snapshot(actor_log) == {**actor_before, "ip_address": None}
    assert snapshot(foreign_log) == foreign_log_before
    versions = (own.version, token.version, contact.version)
    assert all(value == 0 for value in redact_personal_history(target).values())
    own.refresh_from_db()
    token.refresh_from_db()
    contact.refresh_from_db()
    assert (own.version, token.version, contact.version) == versions


@pytest.mark.parametrize("refusal", ["foreign", "shared", "last_owner", "operator"])
def test_api_refusals_precede_every_historical_or_membership_change(stock, refusal):
    org, foreign_org = support._org("admission"), support._org("foreign")
    actor = support._member_manager(org)
    target = support._member(org, support._user("target"))
    if refusal == "foreign":
        Member.objects.filter(user=target).update(scope_id=foreign_org.pk)
    elif refusal == "shared":
        support._member(foreign_org, target)
    elif refusal == "last_owner":
        support._bind(target, stock["org_owner"], "ORG", org.pk)
        actor = target
    else:
        target.is_superuser = True
        target.save()
    row = audit(org, target, target, data={"email": target.email}, actor_display=target.username)
    before = snapshot(row)
    members = list(Member.objects.filter(user=target).values())
    identity = (target.username, target.email, target.is_active)
    response = api(org, actor, target)
    assert not response["ok"] and response["errors"][0]["code"] == "PERMISSION_DENIED"
    assert snapshot(row) == before
    assert list(Member.objects.filter(user=target).values()) == members
    target.refresh_from_db()
    assert (target.username, target.email, target.is_active) == identity
    assert AuditEvent.objects.get(action="identity.user.anonymized").decision == "DENY"


def test_operator_preserves_all_live_member_rows_and_retained_bindings_become_unusable(stock):
    from astrolift_identity.api_tokens import mint_token, verify_token

    org, other_org = support._org("all-members"), support._org("other-members")
    operator = support._user("operator", is_superuser=True)
    target = support._member(other_org, support._member(org, support._user("target")))
    for kind, scope in [("TEAM", 401), ("PROJECT", 402), ("APP", 403)]:
        Member.objects.create(user=target, scope_kind=kind, scope_id=scope, is_active=True)
    removed = Member.objects.create(
        user=target, scope_kind="TEAM", scope_id=999, is_active=True, deleted_at=timezone.now()
    )
    binding = support._bind(target, stock["org_owner"], "ORG", org.pk)
    issued = mint_token()
    token = ApiToken.objects.create(
        user=target, organization=org, name="Owned", token_hash=issued.token_hash, scopes=["admin"]
    )
    assert verify_token(issued.plaintext).pk == token.pk
    client = Client()
    client.force_login(target)
    baseline = client.post(
        "/app/gql/config/",
        {"query": "query { astroliftMyPermissions }"},
        content_type="application/json",
        HTTP_X_PLATFORM="WEB",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(org.guid),
    )
    assert baseline.status_code == 200 and baseline.json().get("data", {}).get("astroliftMyPermissions")
    bearer_client = Client()
    bearer_headers = {
        "HTTP_AUTHORIZATION": f"Bearer {issued.plaintext}",
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(org.guid),
    }
    bearer_before = bearer_client.post(
        "/app/gql/config/",
        {"query": "query { astroliftMyPermissions }"},
        content_type="application/json",
        **bearer_headers,
    )
    assert bearer_before.status_code == 200 and bearer_before.json().get("data", {}).get(
        "astroliftMyPermissions"
    )
    response = api(org, operator, target)
    assert response["ok"] and response["data"]["requiresLogout"] is False
    live = Member.objects.filter(user=target, deleted_at__isnull=True)
    assert live.count() == 5 and not live.filter(is_active=True).exists()
    assert set(live.values_list("lifecycle", flat=True)) == {"deactivated"}
    removed.refresh_from_db()
    assert removed.is_active is True
    assert RoleBinding.objects.filter(pk=binding.pk, deleted_at__isnull=True).exists()
    assert verify_token(issued.plaintext) is None
    session_result = client.post(
        "/app/gql/config/",
        {"query": "query { astroliftMyPermissions }"},
        content_type="application/json",
        HTTP_X_PLATFORM="WEB",
    )
    assert session_result.status_code in (200, 401, 403)
    if session_result.status_code == 200:
        payload = session_result.json()
        assert payload.get("errors") or not payload.get("data", {}).get("astroliftMyPermissions")
    bearer_after = bearer_client.post(
        "/app/gql/config/",
        {"query": "query { astroliftMyPermissions }"},
        content_type="application/json",
        **bearer_headers,
    )
    assert bearer_after.status_code in (200, 401, 403)
    if bearer_after.status_code == 200:
        payload = bearer_after.json()
        assert payload.get("errors") or not payload.get("data", {}).get("astroliftMyPermissions")
