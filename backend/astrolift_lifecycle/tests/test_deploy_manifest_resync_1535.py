"""
resync_manifest_for_deploy (#1535) — deploys refresh the manifest first.

Pins the activity's sync core: it resolves the deployment's app, calls
the one blessed resync service, and reports the outcome instead of
raising — a fetch failure must degrade to the stored manifest, never
fail the deploy.
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


def test_resync_outcome_reported_for_deploys_app(app, env, monkeypatch):
    import astrolift_registry.services.manifest_sync as manifest_sync

    seen: list[str] = []

    class _Result:
        status = "applied"
        error = ""

    def _fake_resync(target_app, **kwargs):
        seen.append(target_app.slug)
        return _Result()

    monkeypatch.setattr(manifest_sync, "resync_app_manifest_from_repo", _fake_resync)
    dep = _deploy(app, env)

    outcome = _resync_manifest_for_deploy_sync(dep.pk)

    assert seen == [app.slug]
    assert outcome["status"] == "applied"
    assert outcome["app_slug"] == app.slug


def test_fetch_failure_is_reported_not_raised(app, env, monkeypatch):
    import astrolift_registry.services.manifest_sync as manifest_sync

    class _Result:
        status = "fetch_failed"
        error = "no source connection available"

    monkeypatch.setattr(manifest_sync, "resync_app_manifest_from_repo", lambda *a, **k: _Result())
    dep = _deploy(app, env)

    outcome = _resync_manifest_for_deploy_sync(dep.pk)

    assert outcome["status"] == "fetch_failed"
    assert "no source connection" in outcome["error"]
