"""Deploy-time manifest resync is visible, not just logged (#1553).

#1535 made every deploy re-fetch ``astrolift.toml`` first, but the refusal
paths (``diverged`` / ``fetch_failed``) rendered the *stored* manifest and
said so only in a worker log line. Since workloads are reconciled only on
``applied``, the operator's experience was a green deploy with zero
workloads and no reason given.

These tests pin the three things that make it visible: the outcome lands on
the Deployment row, a refusal carries the workload count the workflow needs
to fail loudly, and a non-``in_sync`` resync leaves an event trail.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.activities.app_lifecycle import _resync_manifest_for_deploy_sync

pytestmark = pytest.mark.django_db


def _deploy(app, env) -> Deployment:
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


class _Result:
    def __init__(self, status: str, error: str = "") -> None:
        self.status = status
        self.error = error


def _stub_resync(monkeypatch, result: _Result) -> None:
    import astrolift_registry.services.manifest_sync as manifest_sync

    monkeypatch.setattr(manifest_sync, "resync_app_manifest_from_repo", lambda *a, **k: result)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("diverged", "DB has unpushed staged drafts"),
        ("fetch_failed", "'astrolift.toml' not found on 'main'"),
        ("applied", ""),
        ("in_sync", ""),
    ],
)
def test_outcome_is_persisted_on_the_deployment(app, env, monkeypatch, status, error):
    """Every outcome is recorded — including the healthy ones, so the UI can
    distinguish "this deploy used your repo" from "we never checked"."""
    _stub_resync(monkeypatch, _Result(status, error))
    dep = _deploy(app, env)

    _resync_manifest_for_deploy_sync(dep.pk)

    dep.refresh_from_db()
    assert dep.manifest_resync_status == status
    assert dep.manifest_resync_error == error


def test_refusal_reports_workload_count_so_the_workflow_can_fail(app, env, monkeypatch):
    """The zero-workload guard in ``deploy_app`` keys off this count. An app
    that refused its resync *and* has nothing to deploy is the exact shape of
    the silent no-op bug, so the count has to reach the workflow."""
    _stub_resync(monkeypatch, _Result("diverged", "staged draft"))
    dep = _deploy(app, env)

    outcome = _resync_manifest_for_deploy_sync(dep.pk)

    assert outcome["status"] == "diverged"
    assert outcome["workload_count"] == 0


def test_resync_emits_an_event_for_refusals(app, monkeypatch):
    """A refused resync has to leave a trail that outlives the worker's logs."""
    import astrolift_registry.services.manifest_sync as manifest_sync

    emitted: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        manifest_sync,
        "_resync_app_manifest_from_repo",
        lambda *a, **k: _Result("fetch_failed", "boom"),
    )
    monkeypatch.setattr(
        manifest_sync.Event,
        "emit",
        staticmethod(lambda event_type, payload=None, **kw: emitted.append((event_type, payload or {}))),
    )

    result = manifest_sync.resync_app_manifest_from_repo(app)

    assert result.status == "fetch_failed"
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "app.manifest_resync.fetch_failed"
    assert payload["app_slug"] == app.slug
    assert payload["error"] == "boom"


def test_in_sync_does_not_emit(app, monkeypatch):
    """Every deploy resyncs; "nothing changed" is not an audit event."""
    import astrolift_registry.services.manifest_sync as manifest_sync

    emitted: list[str] = []
    monkeypatch.setattr(manifest_sync, "_resync_app_manifest_from_repo", lambda *a, **k: _Result("in_sync"))
    monkeypatch.setattr(
        manifest_sync.Event,
        "emit",
        staticmethod(lambda event_type, payload=None, **kw: emitted.append(event_type)),
    )

    manifest_sync.resync_app_manifest_from_repo(app)

    assert emitted == []


def test_emit_failure_does_not_break_the_resync(app, monkeypatch):
    """Audit is best-effort: an events-backend failure must not turn a
    successful resync into a failed deploy."""
    import astrolift_registry.services.manifest_sync as manifest_sync

    def _boom(*a, **k):
        raise RuntimeError("events backend down")

    monkeypatch.setattr(manifest_sync, "_resync_app_manifest_from_repo", lambda *a, **k: _Result("applied"))
    monkeypatch.setattr(manifest_sync.Event, "emit", staticmethod(_boom))

    result = manifest_sync.resync_app_manifest_from_repo(app)

    assert result.status == "applied"
