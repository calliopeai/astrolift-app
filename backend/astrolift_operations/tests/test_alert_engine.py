"""Tests for the alert-rule evaluation loop (real Postgres).

Covers the sweep's transition model (clear->firing creates an AlertEvent
+ pages; firing->clear resolves; steady state is a no-op), the
tolerate-no-data path (default PromQL rules + a driverless kind rule
produce zero events instead of crashing), the fan-out through the same
``Event.emit`` -> ``notification_dispatch`` path the uptime probe uses
(including the no-NotificationProfile no-op), and mute suppression.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

import core.events as _events_mod
from astrolift_identity.models import Member, Organization, Team
from astrolift_operations import alert_engine, alert_evaluators
from astrolift_operations.alert_seed import seed_default_alert_rules
from astrolift_operations.models import (
    AlertEvent,
    AlertMute,
    AlertRule,
    AuditEvent,
    DeviceRegistration,
)
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
    the suite stays hermetic (mirrors the dispatch test)."""
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


@pytest.fixture
def ensure_dispatcher_subscribed():
    """Guarantee ``dispatch_event`` is subscribed for the fan-out tests,
    restoring the prior subscriber list on teardown."""
    snapshot = list(_events_mod._subscribers)
    register_event_subscriber(dispatch_event)
    yield
    _events_mod._subscribers[:] = snapshot


def _org() -> Organization:
    slug = f"org-{uuid.uuid4().hex[:8]}"
    return Organization.objects.create(name=slug.upper(), slug=slug)


def _rule(org, *, name="rule", predicate=None, is_active=True, severity=None) -> AlertRule:
    return AlertRule.objects.create(
        organization=org,
        name=name,
        target=AlertRule.Target.APP.value,
        target_id="app",
        predicate=dict(predicate or {}),
        severity=severity or AlertRule.Severity.CRITICAL.value,
        is_active=is_active,
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


def _force_evaluate(monkeypatch, fn):
    """Install a controllable ``evaluate`` seen by the engine's sweep."""
    monkeypatch.setattr(alert_evaluators, "evaluate", fn)


# ---- transition model ----------------------------------------------


def test_sweep_fires_alert_event_on_breach(monkeypatch):
    org = _org()
    rule = _rule(org, predicate={"kind": "x"})
    _force_evaluate(monkeypatch, lambda r: True)

    with _CaptureEvents() as cap:
        summary = alert_engine.run_alert_sweep()

    assert summary == {"evaluated": 1, "fired": 1, "resolved": 0}
    events = AlertEvent.objects.filter(rule=rule)
    assert events.count() == 1
    ev = events.first()
    assert ev.resolved_at is None
    assert ev.severity == AlertRule.Severity.CRITICAL.value
    assert ev.organization_id == org.id
    assert ev.summary
    # Fan-out event went out on the same Event.emit path.
    assert cap.kinds().count("alert.fired") == 1
    fired = next(e for e in cap.events if e.event_type == "alert.fired")
    assert fired.organization_id == org.id
    assert fired.payload["rule_guid"] == str(rule.guid)


def test_sweep_no_events_when_no_data(monkeypatch):
    """Default PromQL rules + a driverless kind rule must produce zero
    events (missing data -> no breach), never a crash. Uses the REAL
    evaluator: no metrics backend, no bound driver."""
    org = _org()
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{uuid.uuid4().hex[:6]}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="App",
        slug="app",
        k8s_namespace=f"{org.slug}-app",
        provisioning_status="ready",
    )
    seed_default_alert_rules(app)
    # A kind-based rule with no managed_service -> driver unresolvable -> no-fire.
    _rule(org, name="ses", predicate={"kind": "ses_bounce_rate", "threshold_pct": 0.0})

    with _CaptureEvents() as cap:
        summary = alert_engine.run_alert_sweep()

    assert summary["fired"] == 0
    assert AlertEvent.objects.count() == 0
    assert "alert.fired" not in cap.kinds()


def test_sweep_idempotent_while_firing(monkeypatch):
    org = _org()
    rule = _rule(org, predicate={"kind": "x"})
    _force_evaluate(monkeypatch, lambda r: True)

    with _CaptureEvents() as cap:
        alert_engine.run_alert_sweep()
        second = alert_engine.run_alert_sweep()

    # Still firing on the second tick -> no new row, no second page.
    assert second == {"evaluated": 1, "fired": 0, "resolved": 0}
    assert AlertEvent.objects.filter(rule=rule).count() == 1
    assert cap.kinds().count("alert.fired") == 1


def test_sweep_resolves_when_breach_clears(monkeypatch):
    org = _org()
    rule = _rule(org, predicate={"kind": "x"})

    state = {"fire": True}
    _force_evaluate(monkeypatch, lambda r: state["fire"])

    with _CaptureEvents() as cap:
        alert_engine.run_alert_sweep()  # fires
        state["fire"] = False
        summary = alert_engine.run_alert_sweep()  # resolves

    assert summary == {"evaluated": 1, "fired": 0, "resolved": 1}
    ev = AlertEvent.objects.get(rule=rule)
    assert ev.resolved_at is not None
    assert cap.kinds().count("alert.fired") == 1
    assert cap.kinds().count("alert.resolved") == 1


def test_inactive_rule_is_not_evaluated(monkeypatch):
    org = _org()
    _rule(org, predicate={"kind": "x"}, is_active=False)
    _force_evaluate(monkeypatch, lambda r: True)

    summary = alert_engine.run_alert_sweep()

    assert summary["evaluated"] == 0
    assert AlertEvent.objects.count() == 0


# ---- fan-out through the dispatch path ------------------------------


def test_fanout_reaches_driver(monkeypatch, ensure_dispatcher_subscribed):
    """AlertEvent creation -> Event.emit -> dispatch_event -> driver.send,
    end to end. The org admin's device receives an ``alert.fired`` push."""
    org = _org()
    user = _user()
    _member(user, org)
    _device(user)
    _rule(org, predicate={"kind": "x"})
    _force_evaluate(monkeypatch, lambda r: True)

    driver = MemoryNotificationDriver()
    set_driver_override_for_tests(driver)
    try:
        alert_engine.run_alert_sweep()
    finally:
        unset_driver_override_for_tests()

    categories = [payload.category for _rid, _plat, payload in driver.sent]
    assert "alert.fired" in categories


def test_fanout_no_driver_is_noop(monkeypatch, ensure_dispatcher_subscribed):
    """With no NotificationProfile + no override the dispatcher audits
    ``no_driver`` and drops the send — the sweep still records the event
    and never raises (the fresh-install safety)."""
    org = _org()
    user = _user()
    _member(user, org)
    _device(user)
    rule = _rule(org, predicate={"kind": "x"})
    _force_evaluate(monkeypatch, lambda r: True)

    # No driver override installed, no NotificationProfile row for the org.
    summary = alert_engine.run_alert_sweep()

    assert summary["fired"] == 1
    assert AlertEvent.objects.filter(rule=rule).count() == 1
    # The dispatcher stores per-send extras in the AuditEvent ``data`` blob.
    no_driver = AuditEvent.objects.filter(
        action="notification.push",
        data__status="no_driver",
        data__event_type="alert.fired",
    )
    assert no_driver.exists()


# ---- mute ----------------------------------------------------------


def test_muted_rule_records_event_but_suppresses_page(monkeypatch):
    org = _org()
    rule = _rule(org, predicate={"kind": "x"})
    AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() + timedelta(hours=1),
        reason="planned maintenance",
    )
    _force_evaluate(monkeypatch, lambda r: True)

    with _CaptureEvents() as cap:
        summary = alert_engine.run_alert_sweep()

    # Row is written (timeline intact) but no page goes out.
    assert summary["fired"] == 1
    assert AlertEvent.objects.filter(rule=rule).count() == 1
    assert "alert.fired" not in cap.kinds()
