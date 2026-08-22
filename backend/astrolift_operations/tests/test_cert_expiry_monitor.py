"""Tests for the cert-expiry monitoring sweep (real Postgres).

The policy in ``cert_expiry`` was already covered by
``test_cert_expiry.py``; nothing called it. These cover the caller: which
domains it walks, the per-domain crossing watermark that makes a reminder
page once instead of daily, the renewal-failure escalation, and the
fan-out reaching a notification driver end to end.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

import core.events as _events_mod
from astrolift_identity.models import Member, Organization, Project, Team
from astrolift_lifecycle.models import CustomDomain
from astrolift_operations import cert_expiry_monitor
from astrolift_operations.cert_expiry import CertHealth
from astrolift_operations.models import DeviceRegistration
from astrolift_operations.notification_dispatch import (
    dispatch_event,
    set_driver_override_for_tests,
    unset_driver_override_for_tests,
)
from astrolift_operations.tests.fixtures.notification_driver import (
    MemoryNotificationDriver,
)
from astrolift_registry.models import RegisteredApp
from core.events import register_event_subscriber, unregister_event_subscriber

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Creating a User indexes its Profile into OpenSearch; stub it out so
    the suite stays hermetic (mirrors the alert-engine test)."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


class _CaptureEvents:
    """Context manager recording every emitted event envelope."""

    def __init__(self) -> None:
        self.events: list = []

    def __call__(self, envelope) -> None:
        self.events.append(envelope)

    def __enter__(self) -> _CaptureEvents:
        register_event_subscriber(self)
        return self

    def __exit__(self, *exc) -> None:
        unregister_event_subscriber(self)

    def kinds(self) -> list[str]:
        return [e.event_type for e in self.events]

    def of(self, event_type: str) -> list:
        return [e for e in self.events if e.event_type == event_type]


@pytest.fixture
def ensure_dispatcher_subscribed():
    """Guarantee ``dispatch_event`` is subscribed for the fan-out test,
    restoring the prior subscriber list on teardown."""
    snapshot = list(_events_mod._subscribers)
    register_event_subscriber(dispatch_event)
    yield
    _events_mod._subscribers[:] = snapshot


def _app() -> RegisteredApp:
    slug = f"org-{uuid.uuid4().hex[:8]}"
    org = Organization.objects.create(name=slug.upper(), slug=slug)
    team = Team.objects.create(name="Eng", slug=f"eng-{slug}", organization=org)
    project = Project.objects.create(name="API", slug=f"api-{slug}", team=team)
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug=f"hello-{uuid.uuid4().hex[:8]}",
        k8s_namespace="acme-hello",
        provisioning_status=RegisteredApp.ProvisioningStatus.READY,
    )


def _domain(
    app: RegisteredApp,
    *,
    expires_in_days: int | None,
    renewal_status: str = "auto",
    state: str = CustomDomain.CertificateState.ACTIVE,
    **kwargs,
) -> CustomDomain:
    # The extra minute keeps the day arithmetic off the boundary: an expiry
    # of exactly ``now + N days`` floors to N-1 by the time the sweep reads it.
    expires_at = (
        timezone.now() + timedelta(days=expires_in_days, minutes=1) if expires_in_days is not None else None
    )
    return CustomDomain.objects.create(
        registered_app=app,
        hostname=f"{uuid.uuid4().hex[:8]}.example.com",
        certificate_state=state,
        cert_expires_at=expires_at,
        cert_observability_status=renewal_status,
        **kwargs,
    )


def _member(user, org):
    return Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG.value,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


def _user():
    email = f"u-{uuid.uuid4().hex[:8]}@astrolift.dev"
    return User.objects.create(email=email, username=email.split("@")[0])


def _device(user, *, platform="ios"):
    return DeviceRegistration.objects.create(
        user=user,
        device_token=f"tok-{uuid.uuid4().hex}",
        platform=platform,
        driver="memory",
    )


# ---- selection -----------------------------------------------------


def test_domain_with_no_known_expiry_is_skipped():
    """Nothing to threshold against — and nothing to page about."""
    _domain(_app(), expires_in_days=None, state=CustomDomain.CertificateState.ISSUING)

    with _CaptureEvents() as cap:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert summary == {"checked": 0, "reminders": 0, "escalations": 0}
    assert cap.kinds() == []


def test_soft_deleted_domain_is_skipped():
    domain = _domain(_app(), expires_in_days=3)
    domain.deleted_at = timezone.now()
    domain.save(update_fields=["deleted_at", "updated_at", "version"])

    with _CaptureEvents() as cap:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert summary["checked"] == 0
    assert cap.kinds() == []


def test_healthy_cert_pages_nobody():
    _domain(_app(), expires_in_days=90)

    with _CaptureEvents() as cap:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert summary["checked"] == 1
    assert summary["reminders"] == 0
    assert cap.kinds() == []


# ---- reminder crossings --------------------------------------------


def test_sweep_emits_reminder_for_cert_inside_the_window():
    domain = _domain(_app(), expires_in_days=10)

    with _CaptureEvents() as cap:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert summary["reminders"] == 1
    assert cap.kinds() == ["domain.cert_expiring"]
    payload = cap.of("domain.cert_expiring")[0].payload
    # Tightest crossed threshold is the headline; the whole set rides along.
    assert payload["threshold_days"] == 14
    assert payload["thresholds_crossed"] == [30, 14]
    assert payload["days_until_expiry"] == 10
    assert payload["hostname"] == domain.hostname
    assert payload["health"] == CertHealth.URGENT.value


def test_reminder_is_watermarked_so_it_pages_once():
    """The second sweep must be silent. Without the per-domain watermark
    the same threshold re-fires on every daily tick."""
    _domain(_app(), expires_in_days=10)

    with _CaptureEvents() as first:
        cert_expiry_monitor.run_cert_expiry_sweep()
    with _CaptureEvents() as second:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert first.kinds() == ["domain.cert_expiring"]
    assert second.kinds() == []
    assert summary == {"checked": 1, "reminders": 0, "escalations": 0}


def test_watermark_is_persisted_on_the_row():
    domain = _domain(_app(), expires_in_days=10)
    assert domain.cert_expiry_checked_at is None

    cert_expiry_monitor.run_cert_expiry_sweep()

    domain.refresh_from_db()
    assert domain.cert_expiry_checked_at is not None


def test_the_next_threshold_fires_on_a_later_sweep():
    """Watermarking silences a threshold already paged, not the cert.

    Time is passed in rather than slept: the cert's expiry is fixed and
    ``now`` moves four days, slipping it inside the 7-day threshold.
    """
    now = timezone.now()
    domain = _domain(_app(), expires_in_days=10)

    with _CaptureEvents() as first:
        cert_expiry_monitor.check_domain(domain, now=now)
    with _CaptureEvents() as second:
        cert_expiry_monitor.check_domain(domain, now=now + timedelta(days=4))

    assert first.of("domain.cert_expiring")[0].payload["threshold_days"] == 14
    assert second.of("domain.cert_expiring")[0].payload["threshold_days"] == 7


# ---- renewal-failure escalation ------------------------------------


def test_failed_renewal_inside_the_window_escalates():
    _domain(_app(), expires_in_days=3, renewal_status="failed")

    with _CaptureEvents() as cap:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert summary["escalations"] == 1
    assert "domain.cert_renewal_failed" in cap.kinds()
    payload = cap.of("domain.cert_renewal_failed")[0].payload
    assert payload["health"] == CertHealth.RENEWAL_FAILED.value
    assert payload["renewal_status"] == "failed"


def test_failed_certificate_state_also_escalates():
    """``certificate_state=failed`` is the platform's own record of a
    failed issuance/renewal, even when the driver reported nothing."""
    _domain(
        _app(),
        expires_in_days=2,
        renewal_status="",
        state=CustomDomain.CertificateState.FAILED,
    )

    with _CaptureEvents() as cap:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert summary["escalations"] == 1
    assert "domain.cert_renewal_failed" in cap.kinds()


def test_escalation_repeats_while_the_reminder_does_not():
    """An unresolved renewal failure inside the window re-pages daily; the
    reminder it arrived with does not."""
    _domain(_app(), expires_in_days=3, renewal_status="failed")

    with _CaptureEvents() as first:
        cert_expiry_monitor.run_cert_expiry_sweep()
    with _CaptureEvents() as second:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert first.kinds() == ["domain.cert_expiring", "domain.cert_renewal_failed"]
    assert second.kinds() == ["domain.cert_renewal_failed"]
    assert summary == {"checked": 1, "reminders": 0, "escalations": 1}


def test_failed_renewal_outside_the_window_does_not_escalate():
    _domain(_app(), expires_in_days=20, renewal_status="failed")

    with _CaptureEvents() as cap:
        summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert summary["escalations"] == 0
    assert "domain.cert_renewal_failed" not in cap.kinds()


# ---- sweep robustness ----------------------------------------------


def test_one_bad_domain_does_not_stop_the_sweep(monkeypatch):
    app = _app()
    _domain(app, expires_in_days=10)
    _domain(app, expires_in_days=10)
    real = cert_expiry_monitor.check_domain
    calls: list[str] = []

    def flaky(domain, **kw):
        calls.append(domain.hostname)
        if len(calls) == 1:
            raise RuntimeError("driver exploded")
        return real(domain, **kw)

    monkeypatch.setattr(cert_expiry_monitor, "check_domain", flaky)
    summary = cert_expiry_monitor.run_cert_expiry_sweep()

    assert len(calls) == 2
    assert summary["checked"] == 1
    assert summary["reminders"] == 1


# ---- fan-out through the dispatch path -----------------------------


def test_fanout_reaches_driver(ensure_dispatcher_subscribed):
    """Sweep -> Event.emit -> dispatch_event -> driver.send, end to end.

    Also the guard that the event type is *wired* into the dispatcher: a
    type missing from NOTIFICATION_TEMPLATES is dropped silently, and one
    missing from DEFAULT_PREFERENCES defaults to off, so either omission
    leaves the alert stuck in the activity feed with nobody paged.
    """
    app = _app()
    user = _user()
    _member(user, app.organization)
    _device(user)
    _domain(app, expires_in_days=3, renewal_status="failed")

    driver = MemoryNotificationDriver()
    set_driver_override_for_tests(driver)
    try:
        cert_expiry_monitor.run_cert_expiry_sweep()
    finally:
        unset_driver_override_for_tests()

    categories = [payload.category for _rid, _plat, payload in driver.sent]
    assert "domain.cert_expiring" in categories
    assert "domain.cert_renewal_failed" in categories
