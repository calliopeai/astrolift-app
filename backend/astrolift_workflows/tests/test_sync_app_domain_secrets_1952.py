"""SyncAppDomainWorkflow on the sandboxed runner: secrets before PATCH_INGRESS
(#1952).

``apply_manifests`` re-renders every workload, pod-template digest included
(#1758), so a subdomain rename that only calls ``apply_manifests`` can roll
pods onto the new digest while the literal Secret it names still holds what
the last deploy wrote -- the next real deploy writes the new value but never
rolls the pods, because the digest already matches. update_secrets now runs
first, same order and reason as the deploy path.

Same technique as ``test_deploy_workflows_sandboxed_1758.py``: the real
workflow class runs in Temporal's default sandbox against fake activities
that record scheduling order, plus a legacy-history replay test proving an
in-flight sync from before this shipped still replays.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_workflows.inputs import Actor, SyncAppDomainInput, WorkflowResult
from astrolift_workflows.workflows.sync_app_domain import SyncAppDomainWorkflow

TASK_QUEUE = "astrolift-test"
FIXTURES = Path(__file__).parent / "fixtures"
_ACTOR = Actor(kind="system")

_PLAN = {
    "plans": [
        {
            "app_environment_id": 2,
            "deployment_id": 3,
            "cluster_id": 5,
            "zone": "apps.example.net",
            "zone_id": "Z123",
            "old_hostnames": ["hello.apps.example.net"],
            "new_hostnames": ["hello-v2.apps.example.net"],
            "wildcard_sans": ["*.apps.example.net"],
        }
    ]
}


def fake_activities(
    calls: list[str], *, apply_manifests_raises: bool = False, wait_dns_raises: bool = False
) -> list:
    @activity.defn(name="astrolift.app_domain_sync.plan")
    async def plan_app_domain_sync(registered_app_id: int, previous_subdomain: str) -> dict:
        calls.append("plan_app_domain_sync")
        return _PLAN

    @activity.defn(name="astrolift.deploy.ensure_static_dns")
    async def ensure_static_dns(deployment_id: int) -> dict:
        calls.append("ensure_static_dns")
        return {}

    @activity.defn(name="astrolift.deploy.update_secrets")
    async def update_secrets(deployment_id: int) -> int:
        calls.append("update_secrets")
        return 1

    @activity.defn(name="astrolift.deploy.apply_manifests")
    async def apply_manifests(deployment_id: int) -> dict:
        calls.append("apply_manifests")
        if apply_manifests_raises:
            raise RuntimeError("apiserver rejected the ingress patch")
        return {"created": [], "updated": [], "unchanged": []}

    @activity.defn(name="astrolift.managed_domain.request_wildcard_cert")
    async def request_wildcard_cert_for_zone(cluster_id: int, zone: str, zone_id: str) -> str:
        calls.append("request_wildcard_cert_for_zone")
        return "cert-id"

    @activity.defn(name="astrolift.deploy.wait_dns")
    async def wait_dns(deployment_id: int, timeout_seconds: int = 300) -> int:
        calls.append("wait_dns")
        if wait_dns_raises:
            raise RuntimeError("DNS propagation timed out")
        return 0

    @activity.defn(name="astrolift.app_domain_sync.verify_hostnames")
    async def verify_app_hostnames(hostnames: list[str], timeout_seconds: int) -> dict:
        calls.append("verify_app_hostnames")
        return {"verified": list(hostnames), "unverified": [], "errors": {}}

    @activity.defn(name="astrolift.app_domain_sync.revert_subdomain")
    async def revert_app_subdomain(registered_app_id: int, previous_subdomain: str) -> str:
        calls.append("revert_app_subdomain")
        return previous_subdomain

    return [
        plan_app_domain_sync,
        ensure_static_dns,
        update_secrets,
        apply_manifests,
        request_wildcard_cert_for_zone,
        wait_dns,
        verify_app_hostnames,
        revert_app_subdomain,
    ]


async def run_sandboxed(
    temporal_env,
    workflow_id: str,
    *,
    apply_manifests_raises: bool = False,
    wait_dns_raises: bool = False,
):
    calls: list[str] = []
    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[SyncAppDomainWorkflow],
        activities=fake_activities(
            calls, apply_manifests_raises=apply_manifests_raises, wait_dns_raises=wait_dns_raises
        ),
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            SyncAppDomainWorkflow.run,
            SyncAppDomainInput(registered_app_id=1, previous_subdomain="hello", actor=_ACTOR),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        result: WorkflowResult = await asyncio.wait_for(handle.result(), timeout=180)
    return result, calls, handle


def _scheduled(history: WorkflowHistory) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name.rsplit(".", 1)[-1]
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def _replay(history: WorkflowHistory) -> None:
    await Replayer(
        workflows=[SyncAppDomainWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)


async def test_secrets_are_applied_before_the_ingress_patch(temporal_env):
    result, calls, handle = await run_sandboxed(temporal_env, "sync-domain-secrets-before-apply-fresh")

    assert result.ok is True, result.message
    assert calls.count("update_secrets") == 1
    assert calls.index("update_secrets") < calls.index("apply_manifests")
    await _replay(await handle.fetch_history())


async def test_a_failed_later_step_rolls_back_through_the_same_secrets_first_order(temporal_env):
    """PATCH_INGRESS itself succeeding and a later step (WAIT_PROPAGATION)
    failing is what makes the rollback replay PATCH_INGRESS -- a step
    that never completed has nothing to undo. The rollback replays it
    through the same _run_step code path, so the reordering applies to
    the undo too."""
    result, calls, handle = await run_sandboxed(
        temporal_env, "sync-domain-secrets-before-apply-rollback", wait_dns_raises=True
    )

    assert result.ok is False, result.message
    # Once for the forward attempt, once for the rollback's replay of the step.
    assert calls.count("update_secrets") == 2
    assert calls.count("apply_manifests") == 2
    assert calls.index("update_secrets") < calls.index("apply_manifests")
    assert calls.count("revert_app_subdomain") == 1
    await _replay(await handle.fetch_history())


async def test_a_sync_started_before_the_reorder_still_replays():
    """Captured from the workflow before #1952, on the Temporal test server.
    A worker on this code must replay a sync that was in flight when it
    rolled out, in that run's order (apply_manifests with no update_secrets
    ahead of it)."""
    history = WorkflowHistory.from_json(
        "legacy-sync-app-domain-secrets-capture",
        (FIXTURES / "legacy-sync-app-domain-secrets-after-apply.json").read_text(),
    )
    scheduled = _scheduled(history)
    assert "update_secrets" not in scheduled
    assert "apply_manifests" in scheduled

    await _replay(history)
