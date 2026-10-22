"""Operator-hosted push delivery (#2256), on real Postgres.

* The dispatcher sends an organization's event only to the devices registered
  in that organization, and only through the driver stamped on each row. The
  #2161 review found it sending every live device of a recipient through the
  event organization's driver, so another organization's token could come
  back ``invalid_token`` and be pruned.
* ``apns`` and ``gcp_fcm`` profiles read their keys from files in the
  organization's own notification secrets directory. The real drivers run;
  only the network is replaced, by a recording APNs transport, a recording
  FCM endpoint and a stand-in for Google's token endpoint.
* ``notification_driver_check`` tells an operator what to fix.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import uuid
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from gcp import notification_fcm
from k8s_native import notification_apns
from k8s_native.notification_apns import APNsNotificationDriver, APNsResponse

import core.events as _events_mod
from astrolift_identity.models import Member, Organization
from astrolift_operations.models import (
    AuditEvent,
    DeviceRegistration,
    NotificationPreference,
    NotificationProfile,
)
from astrolift_operations.notification_dispatch import (
    NotificationSecretError,
    _build_driver_from_profile,
    _read_notification_secret,
    dispatch_event,
    notification_secrets_dir,
    unset_driver_override_for_tests,
)
from astrolift_operations.notification_profile import (
    NotificationProfileError,
    validate_notification_driver_config,
)
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    RegisterMobileDeviceInput,
    SetNotificationProfileInput,
)
from core.events import Event, register_event_subscriber
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

INSTALL_ID = "install-2256aa0000000000000000aa"
TOKEN_A = "a1" * 32
TOKEN_B = "b2" * 32
APNS_CONFIG = {
    "team_id": "TEAM123456",
    "key_id": "KEY1234567",
    "topic": "com.example.astrolift",
    "environment": "production",
    "auth_key_secret_ref": "apns-auth-key.p8",
}


@dataclass
class ApnsRecorder:
    """Stands in for Apple: records each request, answers per device token."""

    answers: dict[str, APNsResponse] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)

    def post(self, *, url: str, headers: dict[str, str], body: bytes) -> APNsResponse:
        self.requests.append((url, dict(headers), json.loads(body)))
        return self.answers.get(url.rsplit("/", 1)[-1], APNsResponse(200, f"apple-{len(self.requests)}"))

    def tokens(self) -> list[str]:
        return [url.rsplit("/", 1)[-1] for url, _headers, _body in self.requests]


@dataclass
class FcmRecorder:
    posts: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)

    def post(self, *, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
        self.posts.append((url, dict(headers), json.loads(body)))
        return {"name": f"projects/astrolift-mobile/messages/{len(self.posts)}"}


@dataclass
class GoogleTokenEndpoint:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, url: str, method: str = "GET", body: Any = None, headers: Any = None, **kwargs: Any):
        self.calls.append({"url": url, "body": body})
        data = json.dumps({"access_token": "ya29.operator", "expires_in": 3600}).encode("utf-8")
        return SimpleNamespace(status=200, data=data, headers={})


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture(autouse=True)
def _dispatcher(monkeypatch, settings, tmp_path):
    """Production driver resolution, the real subscriber, a fresh secrets
    directory and no cached provider credentials."""
    monkeypatch.setenv("ASTROLIFT_INSTALL_ID", INSTALL_ID)
    settings.ASTROLIFT_NOTIFICATION_SECRETS_DIR = str(tmp_path / "notifications")
    unset_driver_override_for_tests()
    snapshot = list(_events_mod._subscribers)
    register_event_subscriber(dispatch_event)
    notification_apns._TOKENS.clear()
    notification_apns._TRANSPORTS.clear()
    notification_fcm._SERVICE_ACCOUNTS.clear()
    yield
    _events_mod._subscribers[:] = snapshot
    notification_apns._TOKENS.clear()
    notification_apns._TRANSPORTS.clear()
    notification_fcm._SERVICE_ACCOUNTS.clear()


@pytest.fixture
def apple(monkeypatch) -> ApnsRecorder:
    recorder = ApnsRecorder()
    monkeypatch.setattr(notification_apns, "_shared_transport", lambda **kwargs: recorder)
    return recorder


def _p8(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=slug.title(), slug=f"{slug}-{uuid.uuid4().hex[:6]}")


def _member(org: Organization, user=None):
    user = user or get_user_model().objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.test", username=f"u-{uuid.uuid4().hex[:8]}"
    )
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG.value,
        scope_id=org.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


def _device(user, org, token: str, *, driver: str = "apns", platform: str = "ios") -> DeviceRegistration:
    return DeviceRegistration.objects.create(
        user=user, organization=org, device_token=token, platform=platform, driver=driver
    )


def _write_secret(org: Organization, name: str, content: str | bytes) -> Path:
    path = Path(notification_secrets_dir(org.pk)) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def _apns_org(slug: str, key: ec.EllipticCurvePrivateKey, **config: Any) -> Organization:
    org = _org(slug)
    _write_secret(org, "apns-auth-key.p8", _p8(key))
    NotificationProfile.objects.create(organization=org, driver="apns", config={**APNS_CONFIG, **config})
    return org


def _alert(org: Organization) -> str:
    incident = str(uuid.uuid4())
    Event.emit(
        "alert.fired",
        {
            "rule_name": "5xx surge",
            "severity": "critical",
            "summary": "web is firing",
            "alert_event_guid": incident,
        },
        resource_kind="alert_event",
        resource_id=incident,
        organization_id=org.pk,
    )
    return incident


def _push_audits(org: Organization) -> list[dict[str, Any]]:
    rows = AuditEvent.objects.filter(action="notification.push", organization_id=org.pk).order_by("pk")
    return [{**row.data, "decision": row.decision, "target_id": row.target_id} for row in rows]


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _verified_jwt(authorization: str, key: ec.EllipticCurvePrivateKey) -> tuple[dict, dict]:
    header, claims, signature = authorization.removeprefix("bearer ").split(".")
    raw = _b64decode(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{header}.{claims}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    return json.loads(_b64decode(header)), json.loads(_b64decode(claims))


# ---- the #2161 finding: devices of another organization or driver ------------


def test_an_event_reaches_only_the_devices_registered_in_its_organization(apple) -> None:
    key_a, key_b = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    org_a = _apns_org("acme", key_a)
    org_b = _apns_org("globex", key_b, team_id="TEAM654321", key_id="KEY7654321")
    person = _member(org_a)
    _member(org_b, person)
    device_a = _device(person, org_a, TOKEN_A)
    device_b = _device(person, org_b, TOKEN_B)
    # Apple answers Unregistered for any token org A's key sends to.
    apple.answers = {
        TOKEN_A: APNsResponse(410, reason="Unregistered"),
        TOKEN_B: APNsResponse(410, reason="Unregistered"),
    }

    _alert(org_a)

    assert apple.tokens() == [TOKEN_A]
    device_a.refresh_from_db()
    device_b.refresh_from_db()
    assert device_a.stale_at is not None  # its own driver pruned it
    assert device_b.stale_at is None and device_b.deleted_at is None
    assert [row["target_id"] for row in _push_audits(org_a)] == [str(device_a.pk)]


def test_a_device_registered_for_another_driver_is_skipped_and_never_pruned(apple) -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    person = _member(org)
    device = _device(person, org, TOKEN_A, driver="gcp_fcm")
    apple.answers = {TOKEN_A: APNsResponse(410, reason="Unregistered")}

    _alert(org)

    assert apple.requests == []
    device.refresh_from_db()
    assert device.stale_at is None
    [audit] = _push_audits(org)
    assert (audit["status"], audit["detail"], audit["decision"]) == (
        "driver_mismatch",
        "registered for gcp_fcm",
        "DENY",
    )
    assert audit["driver"] == "apns"


def test_a_registration_from_before_the_profile_is_sent_once_the_app_registers_again(apple) -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    person = _member(org)
    device = _device(person, org, TOKEN_A, driver="unconfigured")
    _alert(org)
    assert apple.requests == []

    request = SimpleNamespace(user=person, session=SimpleNamespace(session_key=""), META={})
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=person.pk)):
        result = OperationsMutation().register_mobile_device(
            info=SimpleNamespace(context=SimpleNamespace(request=request)),
            input=RegisterMobileDeviceInput(device_token=TOKEN_A, platform="ios", label="iPhone"),
        )
    assert result.ok, result.errors
    device.refresh_from_db()
    assert device.driver == "apns"

    _alert(org)
    assert apple.tokens() == [TOKEN_A]


# ---- apns through a real profile ------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "host"), [("production", "api.push.apple.com"), ("sandbox", "api.sandbox.push.apple.com")]
)
def test_an_apns_profile_sends_the_contract_signed_with_the_organization_key(
    apple, environment, host
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    org = _apns_org("acme", key, environment=environment)
    person = _member(org)
    _device(person, org, TOKEN_A)

    incident = _alert(org)

    [(url, headers, body)] = apple.requests
    assert url == f"https://{host}/3/device/{TOKEN_A}"
    assert headers["apns-topic"] == "com.example.astrolift"
    assert headers["apns-push-type"] == "alert"
    assert body == {
        "payload_version": "1",
        "install_id": INSTALL_ID,
        "organization_id": str(org.guid),
        "event_kind": "alert.fired",
        "target_kind": "alert_event",
        "target_id": incident,
        "action_url": f"astrolift://alerts/{incident}",
        "aps": {
            "alert": {"title": "CRITICAL · 5xx surge", "body": "web is firing"},
            "category": "alert.fired",
            "sound": "default",
        },
    }
    jwt_header, claims = _verified_jwt(headers["authorization"], key)
    assert (jwt_header["kid"], claims["iss"]) == ("KEY1234567", "TEAM123456")
    [audit] = _push_audits(org)
    assert (audit["status"], audit["detail"], audit["token_last_4"], audit["driver"]) == (
        "delivered",
        "apple-1",
        TOKEN_A[-4:],
        "apns",
    )


def test_unregistered_prunes_while_an_environment_mismatch_does_not(apple) -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    person = _member(org)
    gone = _device(person, org, TOKEN_A)
    other_environment = _device(person, org, TOKEN_B)
    apple.answers = {
        TOKEN_A: APNsResponse(410, reason="Unregistered"),
        TOKEN_B: APNsResponse(400, reason="BadDeviceToken"),
    }

    _alert(org)

    gone.refresh_from_db()
    other_environment.refresh_from_db()
    assert gone.stale_at is not None and gone.deleted_at is not None
    assert other_environment.stale_at is None and other_environment.deleted_at is None
    statuses = {row["target_id"]: (row["status"], row["detail"]) for row in _push_audits(org)}
    assert statuses[str(gone.pk)] == ("invalid_token", "APNs 410 Unregistered")
    assert statuses[str(other_environment.pk)][0] == "failed"
    assert "other APNs environment" in statuses[str(other_environment.pk)][1]
    # Redacted: the audit keeps the last four characters, never the token.
    assert TOKEN_B not in json.dumps(_push_audits(org))


def test_a_rotated_key_file_signs_the_next_event_without_a_restart(apple) -> None:
    first_key, second_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    org = _apns_org("acme", first_key)
    person = _member(org)
    _device(person, org, TOKEN_A)
    _alert(org)

    _write_secret(org, "apns-auth-key.p8", _p8(second_key))
    NotificationProfile.objects.filter(organization=org).update(
        config={**APNS_CONFIG, "key_id": "KEY7654321"}
    )
    _alert(org)

    first, second = (headers["authorization"] for _url, headers, _body in apple.requests)
    assert _verified_jwt(first, first_key)[0]["kid"] == "KEY1234567"
    assert _verified_jwt(second, second_key)[0]["kid"] == "KEY7654321"


def test_a_removed_key_file_stops_sends_without_touching_devices(apple) -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    person = _member(org)
    device = _device(person, org, TOKEN_A)
    (Path(notification_secrets_dir(org.pk)) / "apns-auth-key.p8").unlink()

    _alert(org)

    assert apple.requests == []
    device.refresh_from_db()
    assert device.deleted_at is None
    assert [row["status"] for row in _push_audits(org)] == ["no_driver"]


def test_a_rejected_provider_key_fails_without_pruning(apple) -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    person = _member(org)
    device = _device(person, org, TOKEN_A)
    apple.answers = {TOKEN_A: APNsResponse(403, reason="InvalidProviderToken")}

    _alert(org)

    device.refresh_from_db()
    assert device.deleted_at is None
    [audit] = _push_audits(org)
    assert audit["status"] == "failed"
    assert "not revoked" in audit["detail"]


def test_preferences_and_revoked_devices_hold_for_apns(apple) -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    opted_out = _member(org)
    NotificationPreference.objects.create(
        user=opted_out, channel="push", event_kind="alert.fired", enabled=False
    )
    _device(opted_out, org, TOKEN_A)
    revoked_owner = _member(org)
    _device(revoked_owner, org, TOKEN_B).soft_delete(by=revoked_owner)

    _alert(org)

    assert apple.requests == []
    statuses = {row["recipient_user_id"]: row["status"] for row in _push_audits(org)}
    assert statuses == {opted_out.pk: "opt_out", revoked_owner.pk: "no_device"}


# ---- secrets stay in the organization's directory -------------------------------


def test_a_profile_cannot_read_another_organizations_key(apple) -> None:
    owner = _apns_org("globex", ec.generate_private_key(ec.SECP256R1()))
    borrower = _org("acme")
    NotificationProfile.objects.create(organization=borrower, driver="apns", config=dict(APNS_CONFIG))
    person = _member(borrower)
    _device(person, borrower, TOKEN_A)

    _alert(borrower)

    assert apple.requests == []
    assert (Path(notification_secrets_dir(owner.pk)) / "apns-auth-key.p8").exists()
    with pytest.raises(NotificationSecretError, match=str(borrower.guid)):
        _build_driver_from_profile(driver_slug="apns", config=dict(APNS_CONFIG), organization_id=borrower.pk)


@pytest.mark.parametrize("ref", ["../globex/apns.p8", "/etc/passwd", ".hidden", "..data", "a/b", "", None, 7])
def test_a_ref_that_is_not_one_file_name_is_refused(ref) -> None:
    org = _org("acme")
    with pytest.raises(NotificationSecretError, match="must name a file"):
        _read_notification_secret(organization_id=org.pk, ref=ref, key="auth_key_secret_ref")
    with pytest.raises(NotificationProfileError, match="auth_key_secret_ref"):
        validate_notification_driver_config(driver="apns", config={**APNS_CONFIG, "auth_key_secret_ref": ref})


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "is empty"),
        (b"\n", "is empty"),
        (b"\xff\xfe-----BEGIN", "not UTF-8"),
        (b"x" * 65537, "larger than"),
    ],
)
def test_unusable_secret_files_are_refused_without_their_content(content, message) -> None:
    org = _org("acme")
    _write_secret(org, "apns-auth-key.p8", content)
    with pytest.raises(NotificationSecretError, match=message) as raised:
        _read_notification_secret(organization_id=org.pk, ref="apns-auth-key.p8", key="auth_key_secret_ref")
    assert "BEGIN" not in str(raised.value)
    assert "xxxx" not in str(raised.value)


def test_a_secret_needs_an_organization() -> None:
    with pytest.raises(NotificationSecretError, match="needs an organization"):
        _read_notification_secret(organization_id=None, ref="apns-auth-key.p8", key="auth_key_secret_ref")


# ---- fcm outside gcp, and the mixed fleet ---------------------------------------


def _service_account() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "astrolift-mobile",
            "private_key_id": uuid.uuid4().hex,
            "private_key": key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ).decode("ascii"),
            "client_email": "push@astrolift-mobile.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@pytest.fixture
def google(monkeypatch) -> tuple[GoogleTokenEndpoint, FcmRecorder]:
    endpoint, fcm = GoogleTokenEndpoint(), FcmRecorder()
    monkeypatch.setattr(notification_fcm, "_google_request", lambda: endpoint)
    monkeypatch.setattr(notification_fcm, "_DefaultFCMHttp", lambda *, timeout: fcm)
    return endpoint, fcm


def test_an_fcm_profile_sends_with_tokens_from_the_service_account_file(google) -> None:
    endpoint, fcm = google
    org = _org("acme")
    _write_secret(org, "fcm-service-account.json", _service_account())
    NotificationProfile.objects.create(
        organization=org,
        driver="gcp_fcm",
        config={"project_id": "astrolift-mobile", "credentials_secret_ref": "fcm-service-account.json"},
    )
    person = _member(org)
    _device(person, org, "fcm-registration-token", driver="gcp_fcm", platform="android")

    incident = _alert(org)
    _alert(org)

    first, second = fcm.posts
    assert first[0] == "https://fcm.googleapis.com/v1/projects/astrolift-mobile/messages:send"
    assert first[1]["Authorization"] == second[1]["Authorization"] == "Bearer ya29.operator"
    assert first[2]["message"]["token"] == "fcm-registration-token"
    assert first[2]["message"]["data"]["target_id"] == incident
    # One token mint for both events: the credentials outlive the per-event driver.
    [call] = endpoint.calls
    assert urllib.parse.parse_qs(call["body"].decode("utf-8"))["grant_type"] == [
        "urn:ietf:params:oauth:grant-type:jwt-bearer"
    ]


def test_a_multiplexer_sends_ios_through_apns_and_android_through_fcm(apple, google) -> None:
    _endpoint, fcm = google
    org = _org("acme")
    _write_secret(org, "apns-auth-key.p8", _p8(ec.generate_private_key(ec.SECP256R1())))
    _write_secret(org, "fcm-service-account.json", _service_account())
    NotificationProfile.objects.create(
        organization=org,
        driver="multiplexer",
        config={
            "primary": {"driver": "apns", "config": dict(APNS_CONFIG)},
            "secondaries": [
                {
                    "driver": "gcp_fcm",
                    "config": {
                        "project_id": "astrolift-mobile",
                        "credentials_secret_ref": "fcm-service-account.json",
                    },
                }
            ],
        },
    )
    person = _member(org)
    _device(person, org, TOKEN_A, driver="multiplexer")
    _device(person, org, "fcm-registration-token", driver="multiplexer", platform="android")

    _alert(org)

    assert apple.tokens() == [TOKEN_A]
    assert [body["message"]["token"] for _url, _headers, body in fcm.posts] == ["fcm-registration-token"]
    assert sorted(row["status"] for row in _push_audits(org)) == ["delivered", "delivered"]


# ---- the profile and the operator check -------------------------------------------


def test_apns_profiles_are_validated() -> None:
    assert validate_notification_driver_config(driver="apns", config=APNS_CONFIG).value == "apns"
    full = {**APNS_CONFIG, "default_ttl_seconds": 0, "timeout_seconds": 5, "max_attempts": 3}
    validate_notification_driver_config(driver="apns", config=full)
    refusals = [
        ({k: v for k, v in APNS_CONFIG.items() if k != "topic"}, "missing required keys"),
        ({**APNS_CONFIG, "auth_key": "-----BEGIN PRIVATE KEY-----"}, "unknown keys"),
        ({**APNS_CONFIG, "team_id": "team"}, "team_id"),
        ({**APNS_CONFIG, "key_id": "KEY12345678"}, "key_id"),
        ({**APNS_CONFIG, "topic": "astrolift"}, "bundle ID"),
        ({**APNS_CONFIG, "environment": "development"}, "'production'"),
        ({**APNS_CONFIG, "environment": ["production"]}, "'production'"),
        ({**APNS_CONFIG, "max_attempts": 4}, "max_attempts"),
        ({**APNS_CONFIG, "default_ttl_seconds": True}, "default_ttl_seconds"),
    ]
    for config, message in refusals:
        with pytest.raises(NotificationProfileError, match=message):
            validate_notification_driver_config(driver="apns", config=config)


def test_fcm_credentials_ref_is_one_file_name() -> None:
    config = {"project_id": "astrolift-mobile", "credentials_secret_ref": "fcm.json"}
    validate_notification_driver_config(driver="gcp_fcm", config=config)
    validate_notification_driver_config(driver="gcp_fcm", config={"project_id": "astrolift-mobile"})
    with pytest.raises(NotificationProfileError, match="credentials_secret_ref"):
        validate_notification_driver_config(
            driver="gcp_fcm", config={**config, "credentials_secret_ref": "projects/p/secrets/fcm"}
        )


def test_set_notification_profile_stores_an_apns_profile(permission_resolver) -> None:
    org = _org("acme")
    user = _member(org)
    permission_resolver.grant(Permission.ORG_UPDATE)
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        result = OperationsMutation().set_notification_profile(
            info, input=SetNotificationProfileInput(driver="apns", config=dict(APNS_CONFIG))
        )
        refused = OperationsMutation().set_notification_profile(
            info,
            input=SetNotificationProfileInput(driver="apns", config={**APNS_CONFIG, "auth_key": "pasted"}),
        )
    assert result.ok, result.errors
    assert NotificationProfile.objects.get(organization=org).config == APNS_CONFIG
    assert not refused.ok
    assert refused.errors[0].code == "VALIDATION"


def test_set_notification_profile_refuses_apns_without_org_update(permission_resolver) -> None:  # noqa: ARG001
    org = _org("acme")
    user = _member(org)
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        result = OperationsMutation().set_notification_profile(
            info, input=SetNotificationProfileInput(driver="apns", config=dict(APNS_CONFIG))
        )
    assert not result.ok
    assert not NotificationProfile.objects.filter(organization=org).exists()


def test_build_driver_apns_builds_the_real_driver(apple) -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    driver = _build_driver_from_profile(driver_slug="apns", config=dict(APNS_CONFIG), organization_id=org.pk)
    assert isinstance(driver, APNsNotificationDriver)
    assert driver.name == "apns"


def test_the_check_command_reports_a_healthy_apns_driver() -> None:
    org = _apns_org("acme", ec.generate_private_key(ec.SECP256R1()))
    out = StringIO()
    call_command("notification_driver_check", organization=org.slug, stdout=out)
    assert out.getvalue().splitlines() == [
        f"organization: {org.slug} ({org.guid})",
        "driver: apns",
        f"secrets directory: {notification_secrets_dir(org.pk)}",
        "health: ok -- apns production com.example.astrolift: provider token signed with key KEY1234567",
    ]


def test_the_check_command_names_what_is_missing() -> None:
    org = _org("acme")
    with pytest.raises(CommandError, match="no active notification profile"):
        call_command("notification_driver_check", organization=org.slug, stdout=StringIO())
    NotificationProfile.objects.create(organization=org, driver="apns", config=dict(APNS_CONFIG))
    with pytest.raises(CommandError, match="no file 'apns-auth-key.p8' in .*" + str(org.guid)):
        call_command("notification_driver_check", organization=org.slug, stdout=StringIO())
    with pytest.raises(CommandError, match="no organization"):
        call_command("notification_driver_check", organization="nobody-here", stdout=StringIO())


def test_the_check_command_fails_on_an_unhealthy_driver() -> None:
    org = _org("acme")
    # A webhook profile with no URL and no SMTP host builds, but reaches nothing.
    NotificationProfile.objects.create(
        organization=org, driver="otlp_webhook", config={"channels": ["webhook"]}
    )
    out = StringIO()
    with pytest.raises(CommandError, match="health check failed"):
        call_command("notification_driver_check", organization=org.slug, stdout=out)
    assert out.getvalue().splitlines()[-1] == (
        "health: failing -- no channels configured (webhook + smtp empty)"
    )
