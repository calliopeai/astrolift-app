"""A managed-domain provisioning run that ends records where and why (#2389).

Two zones on CONFLICT sat at ``configure_cert_policy`` and
``validate_ns_delegation`` with nothing saying the run had ended. The poll
sleeps raised ``TimeoutError`` on their first unsignalled tick, so the run
failed thirty seconds in, and the ``ok=False`` returns never touched the row.
Behind ``domain-terminal-state-2389`` the sleeps keep polling and every
``ok=False`` return records ``provision_state='failed'`` with the step and a
bounded reason; Revalidate restarts the run in the mode the row was
provisioned in.

The Temporal tests run the real workflow on the time-skipping test server with
fake DNS activities and the real failure-recording activity against Postgres.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_clusters.models import ManagedDomain
from astrolift_workflows.activities.provision_managed_domain import (
    _mark_provision_failed_sync,
    mark_managed_domain_provision_failed,
)
from astrolift_workflows.inputs import Actor, ProvisionManagedDomainInput
from astrolift_workflows.workflows.provision_managed_domain import ProvisionManagedDomainWorkflow

TASK_QUEUE = "astrolift-test"
ZONE = "apps.example.com"
FIXTURES = Path(__file__).parent / "fixtures"


def _fake_activities(statuses: list[str], calls: list[str]) -> list:
    """Cert request plus a poll answering ``statuses`` in turn (the last repeats)."""

    @activity.defn(name="astrolift.managed_domain.request_wildcard_cert")
    async def request_cert(cluster_id: int, zone: str, zone_id: str) -> str:
        calls.append("request_wildcard_cert")
        return "cert-1"

    @activity.defn(name="astrolift.managed_domain.poll_cert_issuance")
    async def poll(cluster_id: int, zone: str, cert_id: str) -> dict:
        calls.append("poll_cert_issuance")
        return {"status": statuses.pop(0) if len(statuses) > 1 else statuses[0]}

    @activity.defn(name="astrolift.managed_domain.register_row")
    async def register(cluster_id: int, zone: str, zone_id: str, cert_id: str) -> int:
        calls.append("register_managed_domain_row")
        return 1

    @activity.defn(name="astrolift.managed_domain.validate_ns_delegation")
    async def delegation(cluster_id: int, zone: str) -> dict:
        calls.append("validate_ns_delegation")
        return {"passed": True, "reason": "no platform nameservers recorded"}

    @activity.defn(name="astrolift.managed_domain.mark_active")
    async def mark_active(cluster_id: int, zone: str) -> None:
        calls.append("mark_managed_domain_active")

    return [request_cert, poll, register, delegation, mark_active, mark_managed_domain_provision_failed]


async def _run(temporal_env, statuses: list[str], workflow_id: str):
    calls: list[str] = []
    async with Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[ProvisionManagedDomainWorkflow],
        activities=_fake_activities(statuses, calls),
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    ):
        handle = await temporal_env.client.start_workflow(
            ProvisionManagedDomainWorkflow.run,
            ProvisionManagedDomainInput(
                cluster_id=1, zone=ZONE, is_platform_managed_zone=False, actor=Actor(kind="system")
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=300)
    return result, calls, await handle.fetch_history()


async def _replay(history: WorkflowHistory) -> None:
    await Replayer(
        workflows=[ProvisionManagedDomainWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)


def _domain(org, **fields) -> ManagedDomain:
    return ManagedDomain.objects.create(
        organization=org, zone=ZONE, dns_driver="route53", is_wildcard_managed=True, **fields
    )


@pytest.mark.django_db(transaction=True)
async def test_a_cert_that_never_issues_leaves_the_row_failed_with_the_reason(temporal_env, org):
    await sync_to_async(_domain)(org, provision_state="configure_cert_policy")

    result, calls, history = await _run(temporal_env, ["pending"], "provision-domain-cert-timeout-2389")

    assert result.ok is False
    assert "timed out" in result.message
    # Every tick polled: the sleeps no longer end the run on their first timeout.
    assert calls.count("poll_cert_issuance") == 144
    domain = await sync_to_async(ManagedDomain.objects.get)(zone=ZONE)
    assert domain.provision_state == "failed"
    assert domain.provision_failed_step == "configure_cert_policy"
    assert domain.provision_failure_reason == (
        "certificate not issued within 72 hours; check its DNS validation records"
    )
    await _replay(history)


@pytest.mark.django_db(transaction=True)
async def test_a_cert_issued_after_a_few_polls_activates_the_zone(temporal_env, org):
    await sync_to_async(_domain)(org)

    result, calls, history = await _run(
        temporal_env, ["pending", "pending", "issued"], "provision-domain-issued-later-2389"
    )

    assert result.ok is True, result.message
    assert calls.count("poll_cert_issuance") == 3
    assert calls[-1] == "mark_managed_domain_active"
    domain = await sync_to_async(ManagedDomain.objects.get)(zone=ZONE)
    assert domain.provision_state != "failed"
    await _replay(history)


async def test_a_run_from_before_the_patch_still_replays():
    """Captured from the workflow before #2389: a caller-owned zone whose
    first poll answered pending, after which the unsignalled sleep raised
    TimeoutError and failed the run. A worker on this code must replay it
    the same way, without scheduling the failure-recording activity."""
    history = WorkflowHistory.from_json(
        "legacy-provision-managed-domain-cert-timeout",
        (FIXTURES / "legacy-provision-managed-domain-cert-timeout.json").read_text(),
    )

    await _replay(history)


@pytest.mark.django_db
def test_the_recorded_reason_is_bounded(org):
    _domain(org, provision_state="validate_ns_delegation")

    assert _mark_provision_failed_sync(ZONE, "validate_ns_delegation", "x" * 1000) is True

    domain = ManagedDomain.objects.get(zone=ZONE)
    assert domain.provision_state == "failed"
    assert len(domain.provision_failure_reason) == 300
    assert _mark_provision_failed_sync("nobody.example", "mark_active", "gone") is False


# ---- Revalidate restarts in the mode the row was provisioned in ----------


@pytest.fixture
def starts(monkeypatch):
    recorded: list = []

    def _start(name, args, *, workflow_id, task_queue=None):
        from astrolift_workflows.client import WorkflowHandle

        recorded.append(args[0])
        return WorkflowHandle(workflow_id=workflow_id, run_id="run-1", enqueued=True)

    monkeypatch.setattr("astrolift_clusters.schema.mutations.start_workflow", _start)
    monkeypatch.setattr("astrolift_clusters.schema.mutations.signal_workflow", lambda *a: False)
    return recorded


@pytest.mark.parametrize(
    ("fields", "platform_managed"),
    [
        ({}, True),
        ({"provision_state": "configure_cert_policy", "provision_nameservers": ["ns-1.example"]}, True),
        ({"provision_state": "failed", "provision_failed_step": "provision_dns_zone"}, True),
        ({"provision_state": "failed", "provision_failed_step": "configure_cert_policy"}, False),
        ({"provision_state": "configure_cert_policy"}, False),
        ({"verification_state": "verified"}, False),
    ],
)
@pytest.mark.django_db
def test_revalidate_restarts_with_the_rows_zone_ownership(
    fields, platform_managed, org, cluster, actor, fake_info, permission_resolver, starts
):
    from astrolift_clusters.schema.mutations import ClustersMutation
    from astrolift_graphql import GUID
    from core.permissions import Permission
    from core.tenancy import TenantContext, tenant_context

    _domain(org, **fields)
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = ClustersMutation().revalidate_managed_domain(
            fake_info, cluster_id=GUID(str(cluster.guid)), zone=ZONE
        )

    assert result.ok, result.errors
    assert [s.is_platform_managed_zone for s in starts] == [platform_managed]


def test_failure_reasons_never_carry_exception_text():
    """The row gets the activity's exception class, never its message."""
    from temporalio.exceptions import ActivityError, ApplicationError

    from astrolift_workflows.workflows.provision_managed_domain import _error_kind

    cause = ApplicationError("AccessDenied for key AKIAEXAMPLE", type="ClientError")
    error = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="w",
        activity_type="t",
        activity_id="1",
        retry_state=None,
    )
    error.__cause__ = cause
    assert _error_kind(error) == "ClientError"
    assert _error_kind(ValueError("secret")) == "ValueError"
