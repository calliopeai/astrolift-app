"""
apply_manifests is dry-run gated (#4, spec 07 §4).

``direct_apply.apply_with_dry_run`` existed with no caller: every deploy
went straight to ``driver.apply_manifests(..., dry_run=False)``. The
driver's apply loop collects per-object failures and keeps going, so a
set containing one schema-invalid or RBAC-forbidden object applied every
other object first and only then reported the failure — the namespace
was left half-updated by a deploy that "failed".

These tests pin the gate at the caller: the first round-trip to the
apiserver is a dry-run, and a dry-run rejection aborts before any object
is mutated.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from _sdk.cluster import ApplyError, ApplyResult

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.activities.app_lifecycle import _apply_manifests_sync
from core.app_deploy import AppDeployError

pytestmark = pytest.mark.django_db


_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""


class _RecordingDriver:
    """Records the ``dry_run`` flag of every apply, in order, and fails
    the dry-run pass only when ``dry_run_errors`` is set."""

    def __init__(self, *, dry_run_errors: list[str] | None = None):
        self.dry_runs: list[bool] = []
        self.dry_run_errors = list(dry_run_errors or [])

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
        self.dry_runs.append(dry_run)
        errors = [
            ApplyError(
                kind="Deployment",
                name="hello-app-web",
                namespace=namespace,
                exception_type="ApiException",
                exception_message=msg,
                is_retryable=False,
            )
            for msg in (self.dry_run_errors if dry_run else [])
        ]
        refs = [f"{m.get('kind')}/{m.get('metadata', {}).get('name')}" for m in manifests]
        return ApplyResult(
            created=[] if errors else refs,
            updated=[],
            unchanged=[],
            errors=errors,
        )


@pytest.fixture
def deployment(app, env):
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


def _install(monkeypatch, driver, env):
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda _deployment: (driver, SimpleNamespace(slug=env.tenant_cluster.slug), "acme-hello-app"),
    )


def test_apply_dry_runs_before_it_mutates(monkeypatch, deployment, env):
    driver = _RecordingDriver()
    _install(monkeypatch, driver, env)

    summary = _apply_manifests_sync(deployment.pk)

    # The dry-run has to be the FIRST round-trip, not merely one of two:
    # a gate that ran after the real apply would validate nothing.
    assert driver.dry_runs == [True, False]
    assert summary["created"], "the real apply's result is still what gets reported"


def test_dry_run_rejection_aborts_before_any_object_is_applied(monkeypatch, deployment, env):
    driver = _RecordingDriver(dry_run_errors=["deployments.apps is forbidden"])
    _install(monkeypatch, driver, env)

    with pytest.raises(AppDeployError, match="dry-run rejected the manifest set"):
        _apply_manifests_sync(deployment.pk)

    # Exactly one round-trip, and it was the dry-run: no object reached
    # the live namespace. Asserting only "one call" would pass with the
    # gate removed (that one call would be the real apply).
    assert driver.dry_runs == [True]


def test_real_apply_errors_still_surface(monkeypatch, deployment, env):
    """The gate can't catch everything (a conflict raced in between).
    Errors from the real apply must keep raising as before."""

    class _FailingApply(_RecordingDriver):
        def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
            result = super().apply_manifests(cluster, namespace, manifests, dry_run=dry_run)
            if dry_run:
                return result
            return ApplyResult(
                created=[],
                updated=[],
                unchanged=[],
                errors=[
                    ApplyError(
                        kind="Deployment",
                        name="hello-app-web",
                        namespace=namespace,
                        exception_type="ApiException",
                        exception_message="conflict",
                        is_retryable=False,
                    )
                ],
            )

    driver = _FailingApply()
    _install(monkeypatch, driver, env)

    with pytest.raises(AppDeployError, match="apply_manifests failed"):
        _apply_manifests_sync(deployment.pk)

    assert driver.dry_runs == [True, False]
