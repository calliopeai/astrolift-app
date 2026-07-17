"""
backfill_deployment_supersede — repair stale RUNNING deployments.

Pins the operator backfill contract: among RUNNING deploys for one
(app, env) only the newest stays RUNNING and the rest go SUPERSEDED;
``--dry-run`` mutates nothing; a RUNNING deploy whose app is soft-deleted
is closed regardless of count; and a second run is a no-op.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from astrolift_lifecycle.models import Deployment

pytestmark = pytest.mark.django_db


def _running_deploy(app, env, *, created_at) -> Deployment:
    """Create a Deployment already in RUNNING, with a controlled
    ``created_at``. ``created_at`` is ``auto_now_add`` so it must be set
    after insert via ``update()`` to make ordering deterministic."""
    dep = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
    )
    Deployment.objects.filter(pk=dep.pk).update(created_at=created_at)
    dep.refresh_from_db()
    return dep


def test_supersedes_all_but_newest_running(app, env):
    now = timezone.now()
    oldest = _running_deploy(app, env, created_at=now - timedelta(hours=3))
    middle = _running_deploy(app, env, created_at=now - timedelta(hours=2))
    newest = _running_deploy(app, env, created_at=now - timedelta(hours=1))

    call_command("backfill_deployment_supersede")

    newest.refresh_from_db()
    middle.refresh_from_db()
    oldest.refresh_from_db()

    assert newest.status == Deployment.Status.RUNNING.value
    assert middle.status == Deployment.Status.SUPERSEDED.value
    assert oldest.status == Deployment.Status.SUPERSEDED.value


def test_dry_run_mutates_nothing(app, env):
    now = timezone.now()
    oldest = _running_deploy(app, env, created_at=now - timedelta(hours=2))
    newest = _running_deploy(app, env, created_at=now - timedelta(hours=1))

    call_command("backfill_deployment_supersede", "--dry-run")

    oldest.refresh_from_db()
    newest.refresh_from_db()
    assert oldest.status == Deployment.Status.RUNNING.value
    assert newest.status == Deployment.Status.RUNNING.value


def test_soft_deleted_app_running_deploy_superseded(app, env):
    now = timezone.now()
    dep = _running_deploy(app, env, created_at=now - timedelta(hours=1))

    app.soft_delete()  # sets deleted_at; deployment row survives (no cascade)

    call_command("backfill_deployment_supersede")

    dep.refresh_from_db()
    assert dep.status == Deployment.Status.SUPERSEDED.value


def test_rerun_is_noop(app, env):
    now = timezone.now()
    oldest = _running_deploy(app, env, created_at=now - timedelta(hours=2))
    newest = _running_deploy(app, env, created_at=now - timedelta(hours=1))

    call_command("backfill_deployment_supersede")
    newest.refresh_from_db()
    superseded_after_first = newest.updated_at

    # Second run: newest is the sole RUNNING, nothing left to close.
    call_command("backfill_deployment_supersede")

    newest.refresh_from_db()
    oldest.refresh_from_db()
    assert newest.status == Deployment.Status.RUNNING.value
    assert oldest.status == Deployment.Status.SUPERSEDED.value
    # The still-RUNNING deploy was not touched again.
    assert newest.updated_at == superseded_after_first
