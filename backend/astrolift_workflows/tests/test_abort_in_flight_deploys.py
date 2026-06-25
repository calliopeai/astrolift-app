"""abort_in_flight_deploys teardown activity (#1004 follow-on).

Deleting an app's k8s namespace does NOT touch its Temporal workflow, so a
running DeployAppWorkflow would keep polling a vanished namespace until
timeout, stranding a worker. Teardown now signals the deploy's abort first.
This pins that the activity targets the right (deterministic) workflow id,
records only deploys it actually signalled, and is best-effort per env.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.app_teardown import _abort_in_flight_deploys_sync

pytestmark = pytest.mark.django_db


def test_signals_active_env_with_abort(app, env, monkeypatch):
    calls = []

    def _fake(wf_id, signal_name, *a):
        calls.append((wf_id, signal_name))
        return True

    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", _fake)

    out = _abort_in_flight_deploys_sync(app.pk)

    expected = f"DeployAppWorkflow-{app.guid}-{env.guid}"
    assert out == [expected]
    assert calls == [(expected, "abort")]


def test_not_recorded_when_no_running_deploy(app, env, monkeypatch):
    # signal_workflow returns False when nothing is running (or Temporal is
    # off) — we must not record it or terminate a speculative id.
    monkeypatch.setattr(
        "astrolift_workflows.client.signal_workflow", lambda *a, **k: False
    )
    assert _abort_in_flight_deploys_sync(app.pk) == []


def test_signal_exception_is_swallowed(app, env, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", _boom)
    # Best-effort: an abort failure must not raise out of the activity (it
    # would block teardown); returns the empty signalled-list.
    assert _abort_in_flight_deploys_sync(app.pk) == []
