"""Actual runtime writers persist observed work and logs across pod teardown."""

import asyncio
import importlib
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.worker import Replayer

from astrolift_lifecycle.models import Deployment, DeploymentLog
from astrolift_workflows.activities.app_lifecycle import (
    apply_manifests,
    health_check,
    mark_failed,
    mark_running,
    poll_rollout,
)
from astrolift_workflows.activities.build_image import BuildImageInput, _build_image_sync, _PreparedBuild
from astrolift_workflows.tests.test_build_image import _make_deployment
from core.testing.temporal import temporal_worker
from providers._sdk.cluster import ApplyResult
from providers.k8s_native.build_kaniko import KanikoBuildDriver

pytestmark = pytest.mark.django_db(transaction=True)


class BuildCluster:
    def __init__(self, fail=False, missing=False):
        self.ticks = 0
        self.fail = fail
        self.missing = missing
        self.deleted = False

    def apply_manifests(self, *args, **kwargs):
        return ApplyResult(created=["Job/build"], updated=[], unchanged=[], errors=[])

    def delete_manifests(self, *args):
        self.deleted = True

    def get_workload_status(self, *args):
        self.ticks += 1
        return SimpleNamespace(
            conditions=[]
            if self.ticks == 1
            else [{"type": "Failed" if self.fail else "Complete", "status": "True", "reason": "build failed"}]
        )

    def read_job_pod_logs(self, *args, **kwargs):
        if self.deleted or self.missing:
            raise RuntimeError("pod unavailable")
        return "building clone-secret ARG-secret\n" + ("pushing image\nfinished" if self.ticks > 1 else "")


@pytest.mark.parametrize("outcome", ["success", "failed", "missing-output", "stub"])
def test_real_kaniko_activity_persists_output_and_never_confirms_failed_or_stub_push(monkeypatch, outcome):
    module = importlib.import_module("astrolift_workflows.activities.build_image")
    deployment = _make_deployment(build_strategy="off" if outcome == "stub" else "dockerfile")
    deployment.registered_app.build_args = {"TOKEN": "ARG-secret"}
    deployment.registered_app.save()
    cluster = BuildCluster(fail=outcome == "failed", missing=outcome == "missing-output")
    driver = KanikoBuildDriver(
        cluster_driver=cluster,
        cluster_slug="build",
        service_account="build",
        git_password="clone-secret",
        sleep=lambda seconds: None,
        log_observer=lambda message: module._record_build_output(deployment.pk, message),
    )
    monkeypatch.setattr(
        module,
        "_prepare_build",
        lambda *args: _PreparedBuild(
            driver=driver, registry_driver=object(), repo_name="app", repo_uri="registry/app"
        ),
    )
    monkeypatch.setattr(module, "_read_pushed_digest", lambda *args: "sha256:" + "ab" * 32)
    if outcome == "failed":
        with pytest.raises(RuntimeError, match="image build failed"):
            _build_image_sync(BuildImageInput(deployment.pk, "sha-run", ""))
    else:
        result = _build_image_sync(BuildImageInput(deployment.pk, "sha-run", ""))
        assert result["stub"] == (outcome == "stub")
    # Driver/pod disappearance does not participate in subsequent persisted reads.
    cluster.deleted = True
    rows = list(DeploymentLog.objects.filter(deployment=deployment).order_by("pk"))
    completed = [e.phase for e in rows if e.event == "completed"]
    persisted_text = "\n".join(e.message for e in rows)
    assert "clone-secret" not in persisted_text and "ARG-secret" not in persisted_text
    if outcome == "stub":
        assert rows == []
    elif outcome == "failed":
        assert completed == []
        assert any(e.event == "failed" for e in rows)
    else:
        assert completed == ["build", "push"]
        assert not any(e.phase == "push" and e.event == "started" for e in rows)
        if outcome == "missing-output":
            assert any("capture warning" in e.message for e in rows)
        else:
            output = "\n".join(e.message for e in rows if e.event == "output")
            assert output.count("building") == 1
            assert "clone-secret" not in output and "ARG-secret" not in output
            assert "finished" in output


@workflow.defn(sandboxed=False)
class DeploymentHistoryProbe:
    @workflow.run
    async def run(self, deployment_id: int):
        try:
            for step in (apply_manifests, poll_rollout, health_check, mark_running):
                await workflow.execute_activity(
                    step,
                    deployment_id,
                    start_to_close_timeout=timedelta(seconds=20),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
        except Exception:
            await workflow.execute_activity(
                mark_failed,
                args=[deployment_id, "observed rollout failure"],
                start_to_close_timeout=timedelta(seconds=20),
            )


@pytest.mark.parametrize("failure", [None, "apply", "rollout", "health"])
async def test_real_temporal_activity_boundaries_persist_only_observed_phases_and_replay(
    temporal_env, app, env, monkeypatch, failure
):
    def make_row():
        return Deployment.objects.create(
            registered_app=app, app_environment=env, status="deploying", image_tag="v1"
        )

    deployment = await sync_to_async(make_row)()
    resources = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "web"},
            "spec": {"template": {"spec": {"containers": [{"name": "web", "image": "registry/web:v1"}]}}},
        }
    ]

    class Driver:
        def apply_manifests(self, *args, dry_run=False):
            return ApplyResult(
                errors=["apiserver refused"] if failure == "apply" else [],
                created=["Deployment/web"],
                updated=[],
                unchanged=[],
            )

        def poll_rollout(self, *args):
            return SimpleNamespace(success=failure != "rollout", message="rollout refused", timed_out=False)

        def get_workload_status(self, *args):
            return SimpleNamespace(ready_replicas=0 if failure == "health" else 1, desired_replicas=1)

    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment", lambda row: (Driver(), SimpleNamespace(slug="test"), "test")
    )
    monkeypatch.setattr("core.app_deploy.render_resources_for_deployment", lambda row: resources)
    # The external registry retention driver and SCM reflection are outside the probe.
    monkeypatch.setattr(
        "astrolift_workflows.activities.image_retention.retain_deployment_images", lambda *args: None
    )
    async with temporal_worker(
        temporal_env,
        workflows=[DeploymentHistoryProbe],
        activities=[apply_manifests, poll_rollout, health_check, mark_running, mark_failed],
    ):
        handle = await temporal_env.client.start_workflow(
            DeploymentHistoryProbe.run,
            deployment.pk,
            id="history-" + uuid4().hex,
            task_queue="astrolift-test",
        )
        await asyncio.wait_for(handle.result(), 40)
        history = await handle.fetch_history()
    await Replayer(workflows=[DeploymentHistoryProbe]).replay_workflow(history)

    def observations():
        deployment.refresh_from_db()
        return deployment.status, list(
            DeploymentLog.objects.filter(deployment=deployment).order_by("pk").values_list("phase", "event")
        )

    status, events = await sync_to_async(observations)()
    if failure:
        assert status == "failed"
        assert (failure, "failed") in events
        assert (failure, "completed") not in events
        assert ("health", "healthy") not in events
        assert ("failed", "failed") in events
        later = {"apply": ["rollout", "health"], "rollout": ["health"], "health": []}[failure]
        assert not any(name in later for name, event in events)
    else:
        assert status == "running"
        assert events == [
            ("apply", "started"),
            ("apply", "summary"),
            ("apply", "completed"),
            ("rollout", "started"),
            ("rollout", "completed"),
            ("health", "started"),
            ("health", "completed"),
            ("health", "healthy"),
        ]
