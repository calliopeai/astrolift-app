"""The reconcile workflow reaches the production Azure identity through a real worker (#2279).

``test_azure_workload_identity_2279`` drives the activity's sync helper. These
run ``ReconcileWorkloadIdentityWorkflow`` with the real
``ensure_workload_identity`` activity on a Temporal test server, against
PostgreSQL and the controlled ARM, so the workflow-to-identity seam is proven
too: the activity's committed record and grant state, and the native writes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async

from astrolift_services.models import AzureWorkloadIdentity, ManagedServiceAttachment, WorkloadIdentityGrant
from astrolift_workflows.activities import ensure_workload_identity
from astrolift_workflows.inputs import Actor, ReconcileWorkloadIdentityInput
from astrolift_workflows.tests.test_azure_workload_identity_2279 import (
    BLOB_CONTRIBUTOR,
    BLOB_READER,
    Binding,
    Grant,
    app_service,
    assignments,
    azure_app,
    blob_scope,
    contributor,
    controlled_arm,
    federations,
    subject,
)
from astrolift_workflows.workflows.reconcile_workload_identity import ReconcileWorkloadIdentityWorkflow
from core.testing.azure_arm import ControlledARM
from core.testing.temporal import temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def arm(monkeypatch: Any) -> ControlledARM:
    return controlled_arm(monkeypatch)


async def _run_workflow(temporal_env: Any, state: Any, bindings: dict[str, Binding], run: int) -> dict:
    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        side_effect=lambda service: bindings.get(service.name),
    ):
        async with temporal_worker(
            temporal_env,
            workflows=[ReconcileWorkloadIdentityWorkflow],
            activities=[ensure_workload_identity],
        ):
            return await temporal_env.client.execute_workflow(
                ReconcileWorkloadIdentityWorkflow.run,
                ReconcileWorkloadIdentityInput(
                    registered_app_id=state.app.pk,
                    app_environment_id=state.env.pk,
                    actor=Actor(kind="system"),
                ),
                id=f"ReconcileWorkloadIdentityWorkflow-{state.env.guid}-{run}",
                task_queue="astrolift-test",
            )


async def test_the_reconcile_workflow_creates_records_and_grants_the_identity(
    temporal_env: Any, arm: ControlledARM
) -> None:
    def world() -> Any:
        state = azure_app("tw")
        app_service(state, "blobs")
        return state

    state = await sync_to_async(world)()

    result = await _run_workflow(temporal_env, state, {"blobs": contributor("data")}, 1)

    record = await sync_to_async(AzureWorkloadIdentity.objects.get)()
    assert result["role_arn"] == record.resource_id
    assert result["annotation"]["azure.workload.identity/client-id"] == record.client_id
    assert list(federations(arm, state).values()) == [
        (record.issuer, await sync_to_async(subject)(state, state.env), ["api://AzureADTokenExchange"])
    ]
    assert assignments(arm) == {(record.principal_id, BLOB_CONTRIBUTOR, blob_scope("data"))}
    grant = await sync_to_async(WorkloadIdentityGrant.objects.get)()
    assert grant.state == "applied"


async def test_a_detach_reconciled_by_the_workflow_keeps_every_other_grant(
    temporal_env: Any, arm: ControlledARM
) -> None:
    def world() -> Any:
        state = azure_app("td")
        app_service(state, "blobs")
        from astrolift_services.models import ManagedService

        shared = ManagedService.objects.create(
            project=state.project,
            tenant_cluster=state.cluster,
            kind=ManagedService.Kind.OBJECT_STORE,
            variant="v1",
            name="reports",
            backend_ref="object_store/reports",
            config={},
        )
        state.attachment = ManagedServiceAttachment.objects.create(
            managed_service=shared, app_environment=state.env
        )
        return state

    state = await sync_to_async(world)()
    bindings = {
        "blobs": contributor("data"),
        "reports": Binding([Grant(blob_scope("reports"), ["Storage Blob Data Reader"])]),
    }
    await _run_workflow(temporal_env, state, bindings, 1)
    record = await sync_to_async(AzureWorkloadIdentity.objects.get)()
    operator = arm.seed_assignment(
        scope=blob_scope("reports"),
        principal_id=record.principal_id,
        role_definition_guid=BLOB_READER,
        description="granted by the data team",
    )

    await sync_to_async(state.attachment.soft_delete)()
    await _run_workflow(temporal_env, state, bindings, 2)

    assert assignments(arm) == {
        (record.principal_id, BLOB_CONTRIBUTOR, blob_scope("data")),
        (record.principal_id, BLOB_READER, blob_scope("reports")),
    }
    assert operator in arm.roles
    live = await sync_to_async(
        lambda: sorted(row.managed_service.name for row in WorkloadIdentityGrant.objects.all())
    )()
    assert live == ["blobs"]
