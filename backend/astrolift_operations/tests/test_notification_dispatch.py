"""
Tests for the notification dispatch pipeline (#476 + #499 + #519).

Real Postgres for model writes + an SDK-shaped in-memory driver
(``tests/fixtures/notification_driver.MemoryNotificationDriver``)
for the outbound send path. After #519 the dispatcher targets the
canonical ``_sdk.notification.NotificationDriver`` protocol; the
fixture installs through a test-only override hook
(``set_driver_override_for_tests``).

Exercises:

* Event subscriber wiring -- every ``Event.emit`` flows through the
  dispatcher after the persistent ``Event`` row is written.
* Per-event templates (deploy / secret / alert / cluster / app / session).
* Per-user opt-out via ``NotificationPreference``.
* SDK ``invalid_token`` -> auto-soft-delete the device row.
* Dispatcher excludes the just-issued session's own enrolled device
  for ``auth.session.created`` (#499).
* GraphQL mutations + queries for register / revoke / list /
  preference set.
* #519: dispatcher routes through ``PushTarget`` + the canonical
  ``NotificationPayload``; no ``MemoryNotificationDriver`` import
  in production code; driver resolution falls back to ``no_driver``
  audit when neither override nor ``NotificationProfile`` exists.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import uuid

import pytest
from _sdk.notification import (
    DevicePlatform,
    NotificationPayload,
    PushTarget,
)
from django.contrib.auth import get_user_model

import core.events as _events_mod
from astrolift_identity.models import AstroliftSession, ClientKind, Member, Organization
from astrolift_operations.models import (
    AuditEvent,
    DeviceRegistration,
    NotificationPreference,
    NotificationProfile,
)
from astrolift_operations.notification_dispatch import (
    NOTIFICATION_TEMPLATES,
    _build_driver_from_profile,
    default_driver_slug_for_registration,
    dispatch_event,
    emit_session_created_event,
    resolve_driver_for_recipient,
    set_driver_override_for_tests,
    unset_driver_override_for_tests,
)
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    RegisterMobileDeviceInput,
    RevokeMobileDeviceInput,
    SetNotificationPreferenceInput,
)
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_operations.tests.fixtures.notification_driver import (
    MemoryNotificationDriver,
)
from core.events import (
    Event as EventEmitter,
)
from core.events import (
    register_event_subscriber,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def fake_driver():
    """Install a fresh SDK-shaped fake driver as the dispatcher's
    override for the duration of the test, clear on teardown."""
    driver = MemoryNotificationDriver()
    set_driver_override_for_tests(driver)
    yield driver
    unset_driver_override_for_tests()


@pytest.fixture
def ensure_dispatcher_subscribed():
    """Make sure ``dispatch_event`` is registered as an event
    subscriber for this test even if a sibling test or fixture
    cleared the list. Restores the registry to its prior state on
    teardown so we don't leak across tests."""
    snapshot = list(_events_mod._subscribers)
    register_event_subscriber(dispatch_event)
    yield
    _events_mod._subscribers[:] = snapshot


def _user(email: str | None = None):
    if email is None:
        email = f"u-{uuid.uuid4().hex[:8]}@astrolift.dev"
    return User.objects.create(email=email, username=email.split("@")[0])


def _org(slug: str | None = None) -> Organization:
    slug = slug or f"org-{uuid.uuid4().hex[:6]}"
    return Organization.objects.create(name=slug.upper(), slug=slug)


def _member(user, org):
    return Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG.value,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


def _device(
    *,
    user,
    token: str | None = None,
    platform: str = "ios",
    label: str = "",
    enrolled_session=None,
    organization=None,
    driver: str = "memory",
) -> DeviceRegistration:
    token = token or f"tok-{uuid.uuid4().hex}"
    return DeviceRegistration.objects.create(
        user=user,
        device_token=token,
        platform=platform,
        label=label,
        driver=driver,
        enrolled_session=enrolled_session,
        organization=organization,
    )


# ---- subscriber wiring ----------------------------------------------


def test_event_emit_invokes_dispatcher_after_writer(fake_driver, ensure_dispatcher_subscribed):
    """End-to-end smoke: an emit fires the dispatcher and the
    in-memory driver records the send."""
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user, label="alice@iPhone")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {
                "app_slug": "demo",
                "environment_name": "production",
                "deployment_guid": "d-123",
                "triggerer_user_id": user.pk,
            },
            resource_kind="deployment",
            resource_id="d-123",
        )

    assert len(fake_driver.sent) == 1
    _registration_id, platform, payload = fake_driver.sent[0]
    assert platform == "ios"
    assert payload.title.startswith("Deploy")
    assert "demo" in payload.body
    assert payload.action_url == "astrolift://deployments/d-123"
    # SDK payload carries the event_type via ``category`` and via
    # the ``data`` blob (the dispatcher emits both so the FCM ``data``
    # path stays addressable).
    assert payload.category == "deploy.approved"
    assert payload.data["event_type"] == "deploy.approved"


def test_no_template_skips_dispatch(fake_driver, ensure_dispatcher_subscribed):
    """Unknown event types are silently ignored -- the platform
    emits dozens of low-signal events the dispatcher must not
    pretend to handle."""
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit("unknown.event.type", {})
    assert fake_driver.sent == []


# ---- per-event templates --------------------------------------------


def test_secret_revealed_fans_to_org_admins(fake_driver, ensure_dispatcher_subscribed):
    """secret.revealed targets every org admin; the actor is a
    fellow admin so they show up too."""
    org = _org()
    admin_a = _user()
    admin_b = _user()
    _member(admin_a, org)
    _member(admin_b, org)
    _device(user=admin_a, label="alice")
    _device(user=admin_b, label="bob")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin_a.pk)):
        EventEmitter.emit(
            "secret.revealed",
            {"key": "DB_PASSWORD", "actor_email": "alice@a.io"},
            resource_kind="secret",
            resource_id="s-1",
        )

    assert len(fake_driver.sent) == 2
    titles = {p.title for _t, _p, p in fake_driver.sent}
    assert titles == {"Secret reveal"}
    bodies = {p.body for _t, _p, p in fake_driver.sent}
    assert any("DB_PASSWORD" in b for b in bodies)


def test_alert_fired_includes_severity_in_title(fake_driver, ensure_dispatcher_subscribed):
    org = _org()
    user = _user()
    _member(user, org)
    _device(user=user)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "alert.fired",
            {
                "rule_name": "5xx surge",
                "severity": "critical",
                "summary": "demo prod 5xx > 5%",
                "rule_guid": "r-42",
            },
            resource_kind="alert_rule",
            resource_id="r-42",
        )

    assert len(fake_driver.sent) == 1
    _t, _p, payload = fake_driver.sent[0]
    assert "CRITICAL" in payload.title
    assert "5xx surge" in payload.title
    assert payload.action_url == "astrolift://alerts/r-42"


def test_cluster_bootstrap_failed_targets_admins_only(fake_driver, ensure_dispatcher_subscribed):
    """cluster.bootstrap_failed goes to org admins; non-members
    don't get pushed even if they have a device with the same
    organization stamped."""
    org = _org()
    admin = _user()
    other = _user()
    _member(admin, org)
    _device(user=admin)
    _device(user=other)  # other is NOT a member of org

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.pk)):
        EventEmitter.emit(
            "cluster.bootstrap_failed",
            {"cluster_name": "prod-aws-1", "reason": "iam policy", "cluster_guid": "c-9"},
            resource_kind="cluster",
            resource_id="c-9",
        )

    assert len(fake_driver.sent) == 1
    _t, _p, payload = fake_driver.sent[0]
    assert "prod-aws-1" in payload.body


def test_app_deregister_pending_targets_triggerer_and_admins(fake_driver, ensure_dispatcher_subscribed):
    org = _org()
    admin = _user()
    triggerer = _user()
    _member(admin, org)
    _member(triggerer, org)
    _device(user=admin, label="admin-phone")
    _device(user=triggerer, label="trig-phone")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=triggerer.pk)):
        EventEmitter.emit(
            "app.deregister_pending",
            {
                "app_slug": "demo",
                "app_guid": "a-1",
                "triggerer_user_id": triggerer.pk,
            },
            resource_kind="registered_app",
            resource_id="a-1",
        )

    # Both admin and triggerer get one push each.
    assert len(fake_driver.sent) == 2


def test_deploy_failed_template_targets_approvers_and_triggerer(fake_driver, ensure_dispatcher_subscribed):
    org = _org()
    a = _user()
    b = _user()
    c = _user()
    _member(a, org)
    _member(b, org)
    _member(c, org)
    _device(user=a)
    _device(user=b)
    _device(user=c)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=a.pk)):
        EventEmitter.emit(
            "deploy.failed",
            {
                "app_slug": "demo",
                "environment_name": "production",
                "deployment_guid": "d-9",
                "triggerer_user_id": a.pk,
                "approver_user_ids": [b.pk, c.pk],
            },
        )

    recipients = {pld.title for _t, _p, pld in fake_driver.sent}
    assert recipients == {"Deploy failed"}
    assert len(fake_driver.sent) == 3


# ---- preferences ----------------------------------------------------


def test_opt_out_suppresses_push_and_audits_skip(fake_driver, ensure_dispatcher_subscribed):
    org = _org()
    user = _user()
    _member(user, org)
    _device(user=user)
    NotificationPreference.objects.create(
        user=user,
        channel="push",
        event_kind="deploy.approved",
        enabled=False,
    )

    AuditEvent.objects.all().delete()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    assert fake_driver.sent == []
    audits = list(AuditEvent.objects.filter(action="notification.push").values_list("data", flat=True))
    assert any(a.get("status") == "opt_out" for a in audits)


def test_default_off_for_cli_session_created(fake_driver, ensure_dispatcher_subscribed):
    """Per #499 §E -- CLI sessions default OFF to avoid CI noise."""
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user, label="alice-phone")

    emit_session_created_event(
        user_id=user.pk,
        session_pk=1,  # synthetic -- no real session row for this branch
        session_guid="s-cli",
        client_kind="cli",
        ip_address=None,
        user_agent="curl/8",
        label="github-actions",
        organization_id=org.id,
        occurred_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
    )
    assert fake_driver.sent == []


def test_default_on_for_mobile_session_created(fake_driver, ensure_dispatcher_subscribed):
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user, label="alice-phone")

    emit_session_created_event(
        user_id=user.pk,
        session_pk=99999,  # session_pk doesn't match the device's enrolled_session, so device is NOT excluded
        session_guid="s-mob",
        client_kind="mobile",
        ip_address="192.0.2.1",
        user_agent="AstroliftApp/1.0",
        label="iPhone 17 Pro",
        organization_id=org.id,
        occurred_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
    )
    assert len(fake_driver.sent) == 1


# ---- stale token soft-delete ----------------------------------------


def test_invalid_token_marks_row_stale_and_soft_deletes(fake_driver, ensure_dispatcher_subscribed):
    """SDK ``invalid_token`` status -> dispatcher marks the device
    row stale + soft-deletes (the SDK Protocol contract says this
    status is the only one that drops the device row)."""
    user = _user()
    org = _org()
    _member(user, org)
    device = _device(user=user, label="dead-phone", token="dead-token")
    fake_driver.force_invalid_token.add("dead-token")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    device.refresh_from_db()
    assert device.stale_at is not None
    assert device.deleted_at is not None


def test_failed_send_does_not_mark_stale(fake_driver, ensure_dispatcher_subscribed):
    """A generic ``failed`` status (provider 5xx, bad payload etc.)
    is audit-only -- the dispatcher does NOT drop the device because
    the next send might succeed."""
    user = _user()
    org = _org()
    _member(user, org)
    device = _device(user=user, token="failing-token")
    fake_driver.force_failed.add("failing-token")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    device.refresh_from_db()
    assert device.stale_at is None
    assert device.deleted_at is None


def test_rate_limited_does_not_mark_stale(fake_driver, ensure_dispatcher_subscribed):
    """``rate_limited`` is a transient signal; the dispatcher logs
    it but does not retry inline + does not drop the device."""
    user = _user()
    org = _org()
    _member(user, org)
    device = _device(user=user, token="hot-token")
    fake_driver.force_rate_limited.add("hot-token")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    device.refresh_from_db()
    assert device.stale_at is None
    assert device.deleted_at is None


# ---- #519: SDK-shaped routing ---------------------------------------


def test_dispatcher_emits_pushtarget_with_sdk_platform(fake_driver, ensure_dispatcher_subscribed):
    """The dispatcher must call ``driver.send`` with a ``PushTarget``
    + a canonical ``NotificationPayload`` (no local Protocol)."""

    captured: list[tuple] = []
    original_send = fake_driver.send

    def spy_send(*, target, payload):
        captured.append((target, payload))
        return original_send(target=target, payload=payload)

    fake_driver.send = spy_send  # type: ignore[method-assign]

    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user, platform="android", token="andro-1")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    assert len(captured) == 1
    target, payload = captured[0]
    assert isinstance(target, PushTarget)
    assert target.platform == DevicePlatform.ANDROID
    assert target.registration_id == "andro-1"
    assert isinstance(payload, NotificationPayload)
    assert payload.title.startswith("Deploy")


def test_web_push_platform_maps_to_sdk_web(fake_driver, ensure_dispatcher_subscribed):
    """The DB platform ``web_push`` maps to the SDK enum's ``WEB``
    member (the platforms aren't named identically)."""
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user, platform="web_push", token="webp-1")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    assert len(fake_driver.sent) == 1
    registration_id, platform_str, _payload = fake_driver.sent[0]
    assert registration_id == "webp-1"
    assert platform_str == "web"


# ---- #519: no production-side MemoryNotificationDriver -------------


def test_memory_driver_is_only_in_test_fixtures():
    """The MemoryNotificationDriver must NOT live anywhere under
    ``astrolift_operations/`` except ``tests/fixtures/``. #519's
    reconcile pulled it out of production code; this is a guard
    against a future regression that re-introduces a non-SDK
    fallback driver in the dispatcher path."""
    ops_root = pathlib.Path(
        importlib.import_module("astrolift_operations").__file__,
    ).parent

    for py_file in ops_root.rglob("*.py"):
        rel = py_file.relative_to(ops_root)
        # tests/fixtures is the canonical home for the fixture; the
        # test module itself is allowed to import + use it. Both are
        # test-tree paths, not production.
        if rel.parts and rel.parts[0] == "tests":
            continue
        text = py_file.read_text(encoding="utf-8")
        assert "MemoryNotificationDriver" not in text, (
            f"{rel} mentions MemoryNotificationDriver; production code "
            "must not reference the in-memory driver after #519. Use "
            "tests/fixtures/notification_driver.py instead."
        )


def test_dispatcher_module_does_not_define_local_notification_driver_protocol():
    """The pre-#519 local ``NotificationDriver`` Protocol must be
    gone -- the dispatcher consumes the canonical SDK Protocol."""
    from astrolift_operations import notification_dispatch

    proto = notification_dispatch.NotificationDriver
    assert (
        proto.__module__ == "_sdk.notification"
    ), f"NotificationDriver must come from _sdk.notification; got {proto.__module__!r}"


def test_dispatcher_module_does_not_define_local_notification_payload():
    """Same shape rule for ``NotificationPayload`` -- the dispatcher
    re-exports the SDK type, doesn't define its own."""
    from astrolift_operations import notification_dispatch

    payload_cls = notification_dispatch.NotificationPayload
    assert payload_cls.__module__ == "_sdk.notification"


# ---- #519: driver resolution from NotificationProfile ---------------


def test_no_driver_no_profile_audits_and_drops(ensure_dispatcher_subscribed):
    """With neither a test override nor an active NotificationProfile
    the dispatcher writes a ``no_driver`` audit row and drops the
    send -- no fallback to a memory driver in production."""
    # Explicitly clear any override left by an earlier test run.
    unset_driver_override_for_tests()

    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user)

    AuditEvent.objects.all().delete()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    audits = list(AuditEvent.objects.filter(action="notification.push").values_list("data", flat=True))
    assert any(a.get("status") == "no_driver" for a in audits)


def test_resolve_driver_for_recipient_uses_active_profile(monkeypatch):
    """``resolve_driver_for_recipient`` builds the right per-cloud
    driver from the org's active ``NotificationProfile`` row."""
    unset_driver_override_for_tests()
    org = _org()
    NotificationProfile.objects.create(
        organization=org,
        driver="aws_sns",
        config={
            "region": "us-west-2",
            "platform_applications": {
                "ios": "arn:aws:sns:us-west-2:111:app/APNS/ios",
                "android": "arn:aws:sns:us-west-2:111:app/GCM/android",
            },
        },
    )

    # Stub out boto3 so the SNS driver constructor doesn't try to
    # talk to AWS during import.
    built: list = []

    class _Stub:
        name = "aws_sns"

    def fake_build(*, driver_slug, config):
        built.append((driver_slug, config))
        return _Stub()

    monkeypatch.setattr(
        "astrolift_operations.notification_dispatch._build_driver_from_profile",
        fake_build,
    )
    driver = resolve_driver_for_recipient(user_id=1, organization_id=org.id)
    assert driver is not None
    assert driver.name == "aws_sns"
    assert built == [
        (
            "aws_sns",
            {
                "region": "us-west-2",
                "platform_applications": {
                    "ios": "arn:aws:sns:us-west-2:111:app/APNS/ios",
                    "android": "arn:aws:sns:us-west-2:111:app/GCM/android",
                },
            },
        )
    ]


def test_resolve_driver_for_recipient_test_override_short_circuits():
    """The test override beats the NotificationProfile lookup -- a
    test fixture installed on top of a profile-configured org must
    intercept the send."""
    org = _org()
    NotificationProfile.objects.create(
        organization=org,
        driver="aws_sns",
        config={"region": "us-west-2", "platform_applications": {"ios": "x"}},
    )
    fixture = MemoryNotificationDriver()
    set_driver_override_for_tests(fixture)
    try:
        driver = resolve_driver_for_recipient(user_id=1, organization_id=org.id)
        assert driver is fixture
    finally:
        unset_driver_override_for_tests()


def test_resolve_driver_for_recipient_returns_none_when_unconfigured():
    """No override + no profile -> ``None`` (caller audits ``no_driver``)."""
    unset_driver_override_for_tests()
    assert resolve_driver_for_recipient(user_id=1, organization_id=None) is None
    org = _org()
    assert resolve_driver_for_recipient(user_id=1, organization_id=org.id) is None


def test_resolve_driver_swallows_build_failure_and_returns_none(monkeypatch):
    """A bad NotificationProfile config (e.g. an unknown driver
    slug) must not raise into the dispatcher -- the resolver
    returns ``None`` so the caller audits ``no_driver``."""
    unset_driver_override_for_tests()
    org = _org()
    NotificationProfile.objects.create(
        organization=org,
        driver="bogus_driver",  # not in the per-slug factory
        config={},
    )
    assert resolve_driver_for_recipient(user_id=1, organization_id=org.id) is None


# ---- _build_driver_from_profile per-slug factory --------------------


def test_build_driver_unknown_slug_raises():
    with pytest.raises(ValueError, match="unknown notification driver"):
        _build_driver_from_profile(driver_slug="nope", config={})


def test_build_driver_aws_sns_builds_real_driver_class():
    """The AWS branch builds an actual ``SNSNotificationDriver``
    instance -- the dispatcher then routes ``driver.send`` into
    the canonical per-cloud impl."""
    from aws.notification_sns import SNSNotificationDriver

    # Provide a no-op SNS client so the constructor doesn't try to
    # mint a boto3 client in CI.
    class _NoopSNS:
        def create_platform_endpoint(self, **kw):
            return {"EndpointArn": "arn:fake"}

        def delete_endpoint(self, **kw):
            return None

        def publish(self, **kw):
            return {"MessageId": "fake"}

    # The factory does not accept an injected client; cover the
    # branch with monkeypatch on boto3.client to avoid the env dep.
    import sys

    fake_boto3 = type("_FakeBoto3", (), {"client": staticmethod(lambda *_a, **_kw: _NoopSNS())})()
    sys.modules.setdefault("boto3", fake_boto3)

    driver = _build_driver_from_profile(
        driver_slug="aws_sns",
        config={
            "region": "us-west-2",
            "platform_applications": {
                "ios": "arn:aws:sns:us-west-2:1:app/APNS/x",
            },
        },
    )
    assert isinstance(driver, SNSNotificationDriver)
    assert driver.name == "aws_sns"


def test_build_driver_otlp_webhook_builds_real_driver_class():
    from k8s_native.notification_otlp import WebhookSMTPNotificationDriver

    driver = _build_driver_from_profile(
        driver_slug="otlp_webhook",
        config={
            "channels": ["webhook"],
            "default_webhook_url": "https://example.invalid/notify",
        },
    )
    assert isinstance(driver, WebhookSMTPNotificationDriver)
    assert driver.name == "otlp_webhook"


def test_build_driver_multiplexer_recursively_builds_children():
    from _sdk.notification import MultiplexerNotificationDriver

    driver = _build_driver_from_profile(
        driver_slug="multiplexer",
        config={
            "primary": {
                "driver": "otlp_webhook",
                "config": {"default_webhook_url": "https://x/y"},
            },
            "secondaries": [
                {
                    "driver": "otlp_webhook",
                    "config": {"default_webhook_url": "https://a/b"},
                },
            ],
        },
    )
    assert isinstance(driver, MultiplexerNotificationDriver)


# ---- driver-slug stamp on registration ------------------------------


def test_default_driver_slug_from_active_profile():
    """``register_mobile_device`` stamps the device row with the
    install's active driver slug -- the dispatcher later refuses
    to send through a row whose slug doesn't match the install's
    active driver."""
    unset_driver_override_for_tests()
    org = _org()
    NotificationProfile.objects.create(
        organization=org,
        driver="gcp_fcm",
        config={"project_id": "demo"},
    )
    slug = default_driver_slug_for_registration(organization_id=org.id)
    assert slug == "gcp_fcm"


def test_default_driver_slug_test_override_stamps_memory():
    fixture = MemoryNotificationDriver()
    set_driver_override_for_tests(fixture)
    try:
        slug = default_driver_slug_for_registration(organization_id=None)
        assert slug == "memory"
    finally:
        unset_driver_override_for_tests()


def test_default_driver_slug_unconfigured_when_no_profile():
    unset_driver_override_for_tests()
    org = _org()
    slug = default_driver_slug_for_registration(organization_id=org.id)
    assert slug == "unconfigured"


# ---- #499 session-create excludes own device ------------------------


def test_session_created_skips_enrolled_device(fake_driver, ensure_dispatcher_subscribed):
    """The just-issued session's own device must NOT receive its
    own 'new sign-in' push -- that's spam to the device the user is
    actively holding."""
    user = _user()
    org = _org()
    _member(user, org)

    # Existing session + its device (which we want to receive the push)
    existing_session = AstroliftSession.objects.create(
        user=user,
        session_key=f"existing-key-{uuid.uuid4().hex[:8]}",
        client_kind=ClientKind.MOBILE.value,
        label="existing-phone",
    )
    other_device = _device(user=user, enrolled_session=existing_session, label="other-phone")

    # The just-issued session + its device (which should be excluded)
    new_session = AstroliftSession.objects.create(
        user=user,
        session_key=f"new-key-{uuid.uuid4().hex[:8]}",
        client_kind=ClientKind.MOBILE.value,
        label="new-phone",
    )
    new_device = _device(user=user, enrolled_session=new_session, label="new-phone")

    emit_session_created_event(
        user_id=user.pk,
        session_pk=new_session.pk,
        session_guid=str(new_session.guid),
        client_kind="mobile",
        ip_address="192.0.2.1",
        user_agent="AstroliftApp/1.0",
        label="iPhone",
        organization_id=org.id,
        occurred_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
    )

    # Only `other_device` should receive a push.
    sent_tokens = {tok for tok, _p, _payload in fake_driver.sent}
    assert other_device.device_token in sent_tokens
    assert new_device.device_token not in sent_tokens


def test_session_created_no_other_devices_silent(fake_driver, ensure_dispatcher_subscribed):
    """Per #499 -- silently skip if the user has 0 other devices.
    The device the user is currently signing in on is excluded;
    if it's their only device the dispatcher emits no push (and
    an email-fallback could fire separately -- out of scope here)."""
    user = _user()
    org = _org()
    _member(user, org)

    new_session = AstroliftSession.objects.create(
        user=user,
        session_key=f"only-{uuid.uuid4().hex[:8]}",
        client_kind=ClientKind.MOBILE.value,
        label="only-phone",
    )
    _device(user=user, enrolled_session=new_session)  # the only device

    emit_session_created_event(
        user_id=user.pk,
        session_pk=new_session.pk,
        session_guid=str(new_session.guid),
        client_kind="mobile",
        ip_address="192.0.2.1",
        user_agent="AstroliftApp/1.0",
        label="iPhone",
        organization_id=org.id,
        occurred_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
    )

    assert fake_driver.sent == []


# ---- audit log ------------------------------------------------------


def test_each_dispatch_writes_audit_event(fake_driver, ensure_dispatcher_subscribed):
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user, label="alice")

    AuditEvent.objects.all().delete()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    audits = AuditEvent.objects.filter(action="notification.push")
    assert audits.count() == 1
    row = audits.first()
    assert row.decision == "ALLOW"
    assert row.data["status"] == "delivered"
    assert row.data["event_type"] == "deploy.approved"
    # #519: audit carries the driver name so an operator can tell
    # which provider delivered (or skipped) a given push.
    assert row.data["driver"] == "memory"


def test_invalid_token_audit_is_deny(fake_driver, ensure_dispatcher_subscribed):
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user, token="bad")
    fake_driver.force_invalid_token.add("bad")

    AuditEvent.objects.all().delete()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "deploy.approved",
            {"deployment_guid": "d-1", "triggerer_user_id": user.pk},
        )

    row = AuditEvent.objects.filter(action="notification.push").first()
    assert row is not None
    assert row.decision == "DENY"
    assert row.data["status"] == "invalid_token"


# ---- GraphQL surface ------------------------------------------------


class _FakeRequest:
    """Minimal request stub for resolver tests."""

    def __init__(self, user, *, session_key: str | None = None):
        self.user = user
        self.session = type("S", (), {"session_key": session_key or ""})()
        self.META = {}


class _FakeInfo:
    def __init__(self, request):
        self.context = type("C", (), {"request": request})()


def test_register_mobile_device_creates_row(fake_driver):
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    result = OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(
            device_token="tok-abc-123",
            platform="ios",
            label="alice@iPhone",
        ),
    )
    assert result.ok is True
    assert result.data.platform == "ios"
    assert result.data.label == "alice@iPhone"
    assert result.data.token_last_4 == "-123"
    assert DeviceRegistration.objects.filter(user=user).count() == 1


def test_register_mobile_device_stamps_driver_from_profile():
    """When an org has an active ``NotificationProfile``, the new
    device row carries that driver slug -- no test override here."""
    unset_driver_override_for_tests()
    user = _user()
    org = _org()
    _member(user, org)
    NotificationProfile.objects.create(
        organization=org,
        driver="aws_sns",
        config={"region": "us-west-2", "platform_applications": {"ios": "x"}},
    )

    info = _FakeInfo(_FakeRequest(user))
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        result = OperationsMutation().register_mobile_device(
            info=info,
            input=RegisterMobileDeviceInput(device_token="t-1", platform="ios", label="x"),
        )
    assert result.ok is True
    row = DeviceRegistration.objects.get(user=user)
    assert row.driver == "aws_sns"


def test_register_mobile_device_re_register_resurrects_row(fake_driver):
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(device_token="tok-same", platform="ios", label="first"),
    )
    row = DeviceRegistration.objects.get(user=user)
    row.mark_stale()
    assert row.stale_at is not None

    # Re-registering the same token should resurrect the row.
    result = OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(device_token="tok-same", platform="ios", label="second"),
    )
    assert result.ok is True
    row.refresh_from_db()
    assert row.stale_at is None
    assert row.deleted_at is None
    assert row.label == "second"
    # Still exactly one row -- the resurrect didn't insert a duplicate.
    assert DeviceRegistration.all_objects.filter(user=user).count() == 1


def test_register_mobile_device_rejects_invalid_platform(fake_driver):
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    result = OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(device_token="x", platform="fax-machine"),
    )
    assert result.ok is False
    assert result.errors[0].field == "platform"


def test_register_mobile_device_rejects_empty_token(fake_driver):
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    result = OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(device_token="   ", platform="ios"),
    )
    assert result.ok is False
    assert result.errors[0].field == "deviceToken"


def test_register_mobile_device_unauthenticated_denied():
    info = _FakeInfo(_FakeRequest(_AnonUser()))
    result = OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(device_token="x", platform="ios"),
    )
    assert result.ok is False


class _AnonUser:
    is_authenticated = False
    pk = None


def test_revoke_mobile_device_owner_path(fake_driver):
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(device_token="tok-revoke", platform="ios"),
    )
    row = DeviceRegistration.objects.get(user=user)
    result = OperationsMutation().revoke_mobile_device(
        info=info,
        input=RevokeMobileDeviceInput(id=str(row.guid)),
    )
    assert result.ok is True
    assert result.data.revoked is True
    row.refresh_from_db()
    assert row.deleted_at is not None


def test_revoke_mobile_device_idempotent(fake_driver):
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    OperationsMutation().register_mobile_device(
        info=info,
        input=RegisterMobileDeviceInput(device_token="tok-x", platform="ios"),
    )
    row = DeviceRegistration.objects.get(user=user)
    OperationsMutation().revoke_mobile_device(info=info, input=RevokeMobileDeviceInput(id=str(row.guid)))
    second = OperationsMutation().revoke_mobile_device(
        info=info, input=RevokeMobileDeviceInput(id=str(row.guid))
    )
    assert second.ok is True
    assert second.data.revoked is False


def test_revoke_mobile_device_other_user_denied(fake_driver):
    owner = _user()
    intruder = _user()
    owner_info = _FakeInfo(_FakeRequest(owner))
    OperationsMutation().register_mobile_device(
        info=owner_info,
        input=RegisterMobileDeviceInput(device_token="tok", platform="ios"),
    )
    row = DeviceRegistration.objects.get(user=owner)
    intruder_info = _FakeInfo(_FakeRequest(intruder))
    result = OperationsMutation().revoke_mobile_device(
        info=intruder_info,
        input=RevokeMobileDeviceInput(id=str(row.guid)),
    )
    assert result.ok is False


def test_my_mobile_devices_query_filters_stale(fake_driver):
    user = _user()
    _device(user=user, label="alive")
    stale = _device(user=user, label="dead")
    stale.mark_stale()

    info = _FakeInfo(_FakeRequest(user))
    devices = OperationsQuery().astrolift_my_mobile_devices(info=info)
    labels = {d.label for d in devices}
    assert labels == {"alive"}
    assert len(devices) == 1


def test_set_notification_preference_upserts():
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    result = OperationsMutation().set_notification_preference(
        info=info,
        input=SetNotificationPreferenceInput(channel="push", event_kind="deploy.failed", enabled=False),
    )
    assert result.ok is True
    assert result.data.enabled is False

    # Toggle back on
    result2 = OperationsMutation().set_notification_preference(
        info=info,
        input=SetNotificationPreferenceInput(channel="push", event_kind="deploy.failed", enabled=True),
    )
    assert result2.ok is True
    assert NotificationPreference.objects.filter(user=user).count() == 1
    assert NotificationPreference.objects.get(user=user).enabled is True


def test_set_notification_preference_rejects_invalid_channel():
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    result = OperationsMutation().set_notification_preference(
        info=info,
        input=SetNotificationPreferenceInput(
            channel="carrier-pigeon", event_kind="deploy.failed", enabled=True
        ),
    )
    assert result.ok is False
    assert result.errors[0].field == "channel"


def test_my_notification_preferences_returns_defaults():
    user = _user()
    info = _FakeInfo(_FakeRequest(user))
    rows = OperationsQuery().astrolift_my_notification_preferences(info=info)
    # The dispatcher catalog covers 7 ops events + 5 session
    # sub-kinds = 12 default-rendered prefs. Assert presence of a few
    # known-on / known-off entries by event_kind.
    by_kind = {(r.channel, r.event_kind): r.enabled for r in rows}
    assert by_kind[("push", "deploy.failed")] is True
    assert by_kind[("push", "secret.revealed")] is True
    assert by_kind[("push", "auth.session.created.cli")] is False
    assert by_kind[("push", "auth.session.created.mobile")] is True
    # All synthesized rows have id=None.
    assert all(r.id is None for r in rows)


def test_my_notification_preferences_overlays_explicit_row():
    user = _user()
    NotificationPreference.objects.create(
        user=user,
        channel="push",
        event_kind="deploy.failed",
        enabled=False,
    )
    info = _FakeInfo(_FakeRequest(user))
    rows = OperationsQuery().astrolift_my_notification_preferences(info=info)
    by_kind = {(r.channel, r.event_kind): r for r in rows}
    overridden = by_kind[("push", "deploy.failed")]
    assert overridden.enabled is False
    assert overridden.id is not None


# ---- payload bounds -------------------------------------------------


def test_payload_strings_are_truncated(fake_driver, ensure_dispatcher_subscribed):
    """A pathological emit payload must not produce an over-size
    push -- both APNs and FCM cap at ~4 KiB total."""
    user = _user()
    org = _org()
    _member(user, org)
    _device(user=user)

    big = "x" * 5000
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk)):
        EventEmitter.emit(
            "alert.fired",
            {
                "rule_name": big,
                "severity": "critical",
                "summary": big,
                "rule_guid": "r-1",
            },
        )

    _t, _p, payload = fake_driver.sent[0]
    assert len(payload.title) <= 64
    assert len(payload.body) <= 240
    # Action URL goes through the same truncation cap.
    assert len(payload.action_url) <= 240


# ---- template catalog completeness ---------------------------------


def test_all_pre_519_templates_still_in_catalog():
    """Regression guard: every event template that shipped pre-#519
    must remain in ``NOTIFICATION_TEMPLATES`` so a future
    reconciliation can't silently drop a fan-out kind."""
    expected = {
        "deploy.approved",
        "deploy.rejected",
        "deploy.failed",
        "secret.revealed",
        "alert.fired",
        "cluster.bootstrap_failed",
        "app.deregister_pending",
        "auth.session.created",
    }
    assert expected <= set(NOTIFICATION_TEMPLATES.keys())


# ---- SDK protocol compliance of the test fixture --------------------


def test_memory_driver_implements_sdk_protocol():
    """The fixture must satisfy the SDK ``NotificationDriver``
    Protocol -- if a sibling agent reshapes the Protocol the test
    suite catches the drift before the dispatcher does.

    The SDK Protocol is not ``@runtime_checkable`` (mypy enforces
    conformance at lint time), so we do structural checks here:
    every required method must be present, and ``send``'s keyword
    signature must keep ``target`` + ``payload`` since the
    dispatcher's call shape depends on it.
    """
    fixture = MemoryNotificationDriver()
    # Every required Protocol method must be callable.
    for method in ("register_device", "revoke_device", "send", "send_bulk", "healthcheck"):
        assert callable(getattr(fixture, method)), f"fixture is missing required Protocol method {method!r}"
    # The ``name`` attribute is the registry identifier.
    assert isinstance(fixture.name, str) and fixture.name
    # ``send`` and ``send_bulk`` keep their kw-only signature so the
    # dispatcher's ``driver.send(target=..., payload=...)`` call
    # signature stays load-bearing.
    sig = inspect.signature(fixture.send)
    assert {"target", "payload"} <= set(sig.parameters)
    bulk_sig = inspect.signature(fixture.send_bulk)
    assert {"targets", "payload"} <= set(bulk_sig.parameters)
