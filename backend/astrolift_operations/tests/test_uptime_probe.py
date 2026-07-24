"""Tests for the synthetic uptime prober (real Postgres).

Covers the classification verdict, result recording, and the transition
-> event emission that drives the app.down / app.recovered alerts.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations import uptime_probe
from astrolift_operations.models import AppUptimeResult
from astrolift_registry.models import RegisteredApp
from core.events import register_event_subscriber, unregister_event_subscriber

pytestmark = pytest.mark.django_db


def _make_app(status: str = "ready") -> RegisteredApp:
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(name="Eng", slug="eng", organization=org)
    project = Project.objects.create(name="API", slug="api", team=team)
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello",
        k8s_namespace="acme-hello",
        provisioning_status=status,
    )


class _CaptureEvents:
    """Context manager that records every emitted event envelope."""

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


def _resp(status_code: int):
    m = mock.Mock()
    m.status_code = status_code
    return m


def _seed_result(app: RegisteredApp, *, is_up: bool, status: int) -> None:
    AppUptimeResult.objects.create(
        registered_app=app,
        checked_at=timezone.now(),
        target_url="https://hello.example/healthz",
        status_code=status,
        latency_ms=1,
        is_up=is_up,
        detail="" if is_up else f"HTTP {status}",
    )


# ---- classify_up (pure) --------------------------------------------


@pytest.mark.parametrize(
    "responded,status,expected",
    [
        (True, 200, True),
        (True, 302, True),  # auth redirect: the edge answered, app reachable
        (True, 404, True),  # app responded (wrong path) but is up
        (True, 500, True),  # app erroring but reachable
        (True, 502, False),  # bad gateway: upstream unreachable
        (True, 503, False),
        (True, 504, False),  # the exact outage this feature exists for
        (False, None, False),  # timeout / connection failure
    ],
)
def test_classify_up(responded, status, expected):
    assert uptime_probe.classify_up(responded=responded, status_code=status) is expected


# ---- probe_app: record + transition emission -----------------------


def test_first_probe_down_emits_app_down():
    # No prior state + the app is already DOWN -> page. A never-healthy
    # app is genuinely unreachable; the old "never on first observation"
    # rule left it silent forever ("never-healthy = never alerts").
    app = _make_app()
    with (
        mock.patch.object(uptime_probe, "_probe_url_for", return_value="https://hello.example/healthz"),
        mock.patch.object(uptime_probe.httpx, "get", return_value=_resp(504)),
        _CaptureEvents() as cap,
    ):
        result = uptime_probe.probe_app(app)

    assert result is not None
    assert result.is_up is False
    assert result.status_code == 504
    assert AppUptimeResult.objects.filter(registered_app=app).count() == 1
    assert cap.kinds().count("app.down") == 1
    down = next(e for e in cap.events if e.event_type == "app.down")
    assert down.registered_app_id == app.id
    assert down.organization_id == app.organization_id


def test_first_probe_down_does_not_refire_next_tick():
    # After the first DOWN datapoint a prev row exists, so a second DOWN
    # tick is not a transition and must NOT re-fire — idempotent paging.
    app = _make_app()
    with (
        mock.patch.object(uptime_probe, "_probe_url_for", return_value="https://hello.example/healthz"),
        mock.patch.object(uptime_probe.httpx, "get", return_value=_resp(504)),
    ):
        uptime_probe.probe_app(app)  # first DOWN observation
        with _CaptureEvents() as cap:
            uptime_probe.probe_app(app)  # second DOWN tick

    assert "app.down" not in cap.kinds()
    assert AppUptimeResult.objects.filter(registered_app=app).count() == 2


def test_first_probe_up_does_not_emit():
    # A first observation that is UP has nothing to recover from -> silent.
    app = _make_app()
    with (
        mock.patch.object(uptime_probe, "_probe_url_for", return_value="https://hello.example/healthz"),
        mock.patch.object(uptime_probe.httpx, "get", return_value=_resp(200)),
        _CaptureEvents() as cap,
    ):
        result = uptime_probe.probe_app(app)

    assert result.is_up is True
    assert "app.down" not in cap.kinds()
    assert "app.recovered" not in cap.kinds()


def test_down_transition_emits_app_down():
    # A previously-up app going down is the meaningful page.
    app = _make_app()
    _seed_result(app, is_up=True, status=200)
    with (
        mock.patch.object(uptime_probe, "_probe_url_for", return_value="https://hello.example/healthz"),
        mock.patch.object(uptime_probe.httpx, "get", return_value=_resp(504)),
        _CaptureEvents() as cap,
    ):
        result = uptime_probe.probe_app(app)

    assert result.is_up is False
    assert cap.kinds().count("app.down") == 1
    down = next(e for e in cap.events if e.event_type == "app.down")
    assert down.registered_app_id == app.id
    assert down.organization_id == app.organization_id


def test_recovery_after_down_emits_app_recovered():
    app = _make_app()
    _seed_result(app, is_up=False, status=504)
    with (
        mock.patch.object(uptime_probe, "_probe_url_for", return_value="https://hello.example/healthz"),
        mock.patch.object(uptime_probe.httpx, "get", return_value=_resp(200)),
        _CaptureEvents() as cap,
    ):
        result = uptime_probe.probe_app(app)

    assert result.is_up is True
    assert cap.kinds().count("app.recovered") == 1
    assert "app.down" not in cap.kinds()


def test_no_emit_when_state_unchanged():
    app = _make_app()
    _seed_result(app, is_up=True, status=200)
    with (
        mock.patch.object(uptime_probe, "_probe_url_for", return_value="https://hello.example/healthz"),
        mock.patch.object(uptime_probe.httpx, "get", return_value=_resp(200)),
        _CaptureEvents() as cap,
    ):
        uptime_probe.probe_app(app)

    assert "app.down" not in cap.kinds()
    assert "app.recovered" not in cap.kinds()
    # still recorded the datapoint (for uptime % / history)
    assert AppUptimeResult.objects.filter(registered_app=app).count() == 2


def test_timeout_is_down_with_detail():
    app = _make_app()
    _seed_result(app, is_up=True, status=200)  # prior up so the timeout transitions
    with (
        mock.patch.object(uptime_probe, "_probe_url_for", return_value="https://hello.example/healthz"),
        mock.patch.object(uptime_probe.httpx, "get", side_effect=RuntimeError("ConnectTimeout")),
        _CaptureEvents() as cap,
    ):
        result = uptime_probe.probe_app(app)

    assert result.is_up is False
    assert result.status_code is None
    assert "ConnectTimeout" in result.detail
    assert cap.kinds().count("app.down") == 1


def test_probe_skips_app_without_managed_hostname():
    app = _make_app()
    with mock.patch.object(uptime_probe, "_probe_url_for", return_value=None):
        assert uptime_probe.probe_app(app) is None
    assert AppUptimeResult.objects.filter(registered_app=app).count() == 0
