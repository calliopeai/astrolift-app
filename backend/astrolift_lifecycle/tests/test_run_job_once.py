"""Tests for the manual run-scheduled-job-once mutation (#390).

Service layer and mutation envelope, both against a recording fake
cluster driver. The manifest pipeline is exercised end-to-end — we
write a real ``astrolift.toml`` onto the test app's
``manifest_raw`` so the renderer feeds the service layer the same
way it would in production.

Coverage:

* Happy path: Job dict built from the cronjob's ``jobTemplate``,
  ``apply_manifests`` called, ``ScheduledJobRun`` recorded with
  ``trigger_kind="manual"``, ``run_name`` shaped
  ``<slug>-manual-<8hex>``.
* Cronjob slug not in manifest → VALIDATION.
* App has no RUNNING Deployment → PRECONDITION.
* Permission gate: APP_READ-only actor → PERMISSION_DENIED.
* Apply failure surfaces in ``MutationResult.errors`` with the
  driver's error message.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.models.jobs import ScheduledJobRun
from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    RunJobOnceInput,
)
from astrolift_lifecycle.services.job_runner import (
    JobRunError,
    run_job_once,
)
from astrolift_registry.models import Container, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


_MANIFEST_TOML = """
name = "hello-app"

[[workloads]]
name = "migrate"
kind = "cronjob"
schedule = "0 * * * *"

  [[workloads.containers]]
  name = "migrate"
  is_primary = true
"""


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _ApplyResult:
    ok: bool
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclasses.dataclass
class _ApplyCall:
    cluster_slug: str
    namespace: str
    resources: list[dict[str, Any]]


class _RecordingDriver:
    """Minimal stand-in for ClusterDriver — records each
    ``apply_manifests`` call and returns a stubbed ``ApplyResult``."""

    def __init__(self, *, result: _ApplyResult | None = None):
        self.calls: list[_ApplyCall] = []
        self._result = result or _ApplyResult(ok=True, created=("Job/migrate",))

    def apply_manifests(
        self,
        cluster_slug: str,
        namespace: str,
        resources: list[dict[str, Any]],
    ) -> _ApplyResult:
        self.calls.append(
            _ApplyCall(
                cluster_slug=cluster_slug,
                namespace=namespace,
                resources=list(resources),
            ),
        )
        return self._result


@pytest.fixture
def install_driver(monkeypatch):
    """Install ``driver`` as the resolver result for the cluster's
    plugin. Mirrors the pattern from ``test_restart_scale_workload``."""

    def _install(driver: Any) -> None:
        def _resolve(cluster):  # noqa: ARG001 — signature parity
            return driver

        # ``core.app_deploy`` imports ``_driver_for_cluster`` into its
        # own module namespace, so the patch must target both module
        # bindings to intercept the apply path.
        monkeypatch.setattr("core.cluster_management._driver_for_cluster", _resolve)
        monkeypatch.setattr("core.app_deploy._driver_for_cluster", _resolve)

    return _install


# ---------------------------------------------------------------------------
# Fixtures — cronjob-shaped app
# ---------------------------------------------------------------------------


@pytest.fixture
def cronjob_app(app):
    """Hydrate ``app`` with a cronjob workload + container row and
    a matching TOML manifest. The renderer reads ``manifest_raw``,
    so we round-trip the TOML through the model rather than wiring
    the workload + container alone."""
    app.manifest_raw = _MANIFEST_TOML
    app.save(update_fields=["manifest_raw"])

    workload = Workload.objects.create(
        registered_app=app,
        name="migrate",
        slug="migrate",
        kind=Workload.Kind.CRONJOB,
        schedule="0 * * * *",
    )
    Container.objects.create(
        workload=workload,
        name="migrate",
        is_primary=True,
        image_ref="",
    )
    return app


@pytest.fixture
def running_deployment(cronjob_app, env, actor):
    return Deployment.objects.create(
        registered_app=cronjob_app,
        app_environment=env,
        triggered_by_user_id=actor.id,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
    )


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _err_code(result) -> str:
    code = result.errors[0].code
    return getattr(code, "value", code)


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


def test_run_job_once_builds_job_from_cronjob_and_applies(
    cronjob_app,
    env,
    running_deployment,
    actor,
    install_driver,
):
    driver = _RecordingDriver()
    install_driver(driver)

    result = run_job_once(
        cronjob_app,
        env.name,
        "migrate",
        actor_user_id=actor.id,
        actor_display="actor@test",
    )

    assert result.ok is True
    assert re.fullmatch(r"migrate-manual-[0-9a-f]{8}", result.run_name)
    assert result.namespace.endswith("-hello-app")
    assert result.logs_url == f"/apps/{cronjob_app.slug}/jobs"

    # One apply call, one resource, Job shape derived from CronJob.
    assert len(driver.calls) == 1
    call = driver.calls[0]
    assert len(call.resources) == 1
    job = call.resources[0]
    assert job["kind"] == "Job"
    assert job["apiVersion"] == "batch/v1"
    assert job["metadata"]["name"] == result.run_name
    assert job["metadata"]["namespace"] == result.namespace
    # backoffLimit pinned to 0 — manual one-shot is single-attempt.
    assert job["spec"]["backoffLimit"] == 0
    # podSpec inherited from the cronjob's jobTemplate.
    template_spec = job["spec"]["template"]["spec"]
    assert isinstance(template_spec.get("containers"), list)
    assert template_spec["containers"][0]["name"] == "migrate"
    # Trigger annotations carry the actor display.
    annotations = job["metadata"]["annotations"]
    assert annotations["astrolift.io/trigger-kind"] == "manual"
    assert annotations["astrolift.io/triggered-by"] == "actor@test"

    # ScheduledJobRun row landed with the manual trigger kind.
    run_row = ScheduledJobRun.objects.get(k8s_job_name=result.run_name)
    assert run_row.trigger_kind == ScheduledJobRun.TriggerKind.MANUAL
    assert run_row.triggered_by_id == actor.id
    assert run_row.app_environment_id == env.id
    assert run_row.namespace == result.namespace
    assert run_row.status == ScheduledJobRun.Status.RUNNING


def test_run_job_once_unknown_slug_raises_validation(
    cronjob_app,
    env,
    running_deployment,
    install_driver,
):
    install_driver(_RecordingDriver())
    with pytest.raises(JobRunError) as excinfo:
        run_job_once(cronjob_app, env.name, "does-not-exist")
    assert excinfo.value.code == "VALIDATION"
    assert "does-not-exist" in excinfo.value.message


def test_run_job_once_without_running_deploy_raises_precondition(
    cronjob_app,
    env,
    install_driver,
):
    """No RUNNING Deployment — refuse to apply (the namespace + image
    aren't ready yet)."""
    install_driver(_RecordingDriver())
    with pytest.raises(JobRunError) as excinfo:
        run_job_once(cronjob_app, env.name, "migrate")
    assert excinfo.value.code == "PRECONDITION"
    assert "no successful deployment" in excinfo.value.message


def test_run_job_once_apply_failure_returns_ok_false(
    cronjob_app,
    env,
    running_deployment,
    install_driver,
):
    """Driver apply error surfaces as ``ok=False`` + ``error`` string
    so the mutation maps it to ``MutationResult.errors`` rather than
    raising."""
    failing = _RecordingDriver(
        result=_ApplyResult(ok=False, errors=("admission webhook denied",)),
    )
    install_driver(failing)

    result = run_job_once(cronjob_app, env.name, "migrate")

    assert result.ok is False
    assert result.error == "admission webhook denied"
    # No ScheduledJobRun row on a failed apply — recording would lie.
    assert not ScheduledJobRun.objects.filter(k8s_job_name=result.run_name).exists()


# ---------------------------------------------------------------------------
# Mutation envelope
# ---------------------------------------------------------------------------


def test_run_job_once_mutation_happy_path(
    cronjob_app,
    env,
    running_deployment,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.run_astrolift_job_once(
            fake_info,
            input=RunJobOnceInput(
                app_slug=cronjob_app.slug,
                environment_name=env.name,
                job_slug="migrate",
            ),
        )

    assert result.ok, result.errors
    assert re.fullmatch(r"migrate-manual-[0-9a-f]{8}", result.data.run_name)
    assert result.data.namespace.endswith("-hello-app")
    assert result.data.logs_url == f"/apps/{cronjob_app.slug}/jobs"


def test_run_job_once_mutation_permission_denied_for_read_only_actor(
    cronjob_app,
    env,
    running_deployment,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    # APP_READ only — no APP_DEPLOY grant.
    permission_resolver.grant(Permission.APP_READ)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.run_astrolift_job_once(
            fake_info,
            input=RunJobOnceInput(
                app_slug=cronjob_app.slug,
                environment_name=env.name,
                job_slug="migrate",
            ),
        )

    assert result.ok is False
    assert _err_code(result) == "PERMISSION_DENIED"


def test_run_job_once_mutation_unknown_job_returns_validation(
    cronjob_app,
    env,
    running_deployment,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.run_astrolift_job_once(
            fake_info,
            input=RunJobOnceInput(
                app_slug=cronjob_app.slug,
                environment_name=env.name,
                job_slug="not-a-job",
            ),
        )

    assert result.ok is False
    assert _err_code(result) == "VALIDATION"
    assert "not-a-job" in result.errors[0].message


def test_run_job_once_mutation_without_deploy_returns_precondition(
    cronjob_app,
    env,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.run_astrolift_job_once(
            fake_info,
            input=RunJobOnceInput(
                app_slug=cronjob_app.slug,
                environment_name=env.name,
                job_slug="migrate",
            ),
        )

    assert result.ok is False
    assert _err_code(result) == "PRECONDITION"
    assert "no successful deployment" in result.errors[0].message


def test_run_job_once_mutation_apply_failure_surfaces_in_errors(
    cronjob_app,
    env,
    running_deployment,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    install_driver(
        _RecordingDriver(
            result=_ApplyResult(ok=False, errors=("server unreachable",)),
        ),
    )
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.run_astrolift_job_once(
            fake_info,
            input=RunJobOnceInput(
                app_slug=cronjob_app.slug,
                environment_name=env.name,
                job_slug="migrate",
            ),
        )

    assert result.ok is False
    assert _err_code(result) == "INTERNAL"
    assert "server unreachable" in result.errors[0].message


def test_run_job_once_mutation_unknown_app_returns_not_found(
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.run_astrolift_job_once(
            fake_info,
            input=RunJobOnceInput(
                app_slug="no-such-app",
                environment_name="prod",
                job_slug="migrate",
            ),
        )

    assert result.ok is False
    assert _err_code(result) == "NOT_FOUND"
