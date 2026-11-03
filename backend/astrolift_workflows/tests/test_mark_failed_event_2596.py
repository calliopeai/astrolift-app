"""A terminal deploy failure reaches the app events feed (#2596).

``deploy.started`` was emitted on dispatch, but a failed deploy wrote only
the row. The app's events feed then showed a deploy that started and never
said how it ended. ``mark_failed`` now emits ``deploy.failed`` with the
reason. These tests stub the ORM and the emitters so they run without a
database or the stack.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import astrolift_lifecycle.models as lifecycle_models
from astrolift_workflows.activities import app_lifecycle


def _deployment() -> SimpleNamespace:
    d = mock.MagicMock()
    d.guid = "11111111-2222-3333-4444-555555555555"
    d.status = "deploying"
    d.aborted_reason = ""
    d.registered_app_id = 9
    d.registered_app.organization_id = 7
    d.app_environment = mock.MagicMock()
    return d


@pytest.fixture
def emitted(monkeypatch):
    d = _deployment()
    fake_models = mock.MagicMock()
    fake_models.Deployment.all_objects.get.return_value = d
    # The activity compares against Deployment.Status.FAILED, so the fake must
    # carry the real enum or the terminal-state check never matches.
    fake_models.Deployment.Status = lifecycle_models.Deployment.Status
    monkeypatch.setattr(lifecycle_models, "Deployment", fake_models.Deployment)
    monkeypatch.setattr(app_lifecycle, "_record_reachability", lambda *a, **k: None)

    import astrolift_lifecycle.github_reflection as reflection
    import core.events as events

    monkeypatch.setattr(reflection, "reflect_deploy_failed", lambda *a, **k: None)
    calls: list[tuple] = []
    monkeypatch.setattr(events.Event, "emit", staticmethod(lambda *a, **k: calls.append((a, k))))
    return d, calls


def test_a_failed_deploy_emits_a_deploy_failed_event_with_the_reason(emitted):
    d, calls = emitted

    app_lifecycle._mark_failed_sync(42, "rollout workload/app failed: crashloop")

    assert d.aborted_reason == "rollout workload/app failed: crashloop"
    assert len(calls) == 1
    (event_type,), kwargs = calls[0]
    assert event_type == "deploy.failed"
    assert kwargs["payload"]["reason"] == "rollout workload/app failed: crashloop"
    assert kwargs["payload"]["severity"] == "error"
    assert kwargs["registered_app_id"] == 9
    assert kwargs["organization_id"] == 7
    assert kwargs["resource_kind"] == "deployment"


def test_a_repeated_mark_failed_emits_no_second_event(emitted):
    d, calls = emitted
    d.status = "failed"

    app_lifecycle._mark_failed_sync(42, "again")

    assert calls == []
