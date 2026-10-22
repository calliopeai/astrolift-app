"""The product-deployment scan is scheduled, served and reaches the clusters (#2465).

A scan absent from the catalog never runs; one in the catalog but held is
registered nowhere; one whose workflow or activity no worker serves fails
the first time it fires. These pin each link, and a real Temporal run of
the tick proves it reaches the scan and records what a cluster runs.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from asgiref.sync import sync_to_async

from astrolift_workflows.activities.scheduled import (
    _reconcile_product_deployments_sync,
    reconcile_product_deployments,
)
from astrolift_workflows.schedule_boot import PHASE_3A_ACTIVE_KINDS, resolve_active_kinds
from astrolift_workflows.schedule_registry import ScheduleKind, get_schedule
from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS
from astrolift_workflows.workflows.scheduled import ReconcileProductDeploymentsWorkflow
from core.testing.temporal import temporal_worker

WORKBENCH_LABELS = {
    "calliope.ai/product-contract": "1",
    "calliope.ai/product": "workbench",
    "calliope.ai/deployment": "acme",
    "calliope.ai/managed-by": "deployment-manager",
}
WORKBENCH_ANNOTATIONS = {
    "calliope.ai/images": '{"hub":"ghcr.io/calliopeai/junohub:0.1.60"}',
    "calliope.ai/endpoints": '{"hub":"https://hub.acme.example.com"}',
    "calliope.ai/workloads": "deployment/hub",
}


def test_the_scan_is_in_the_schedule_catalog_every_two_minutes():
    schedule = get_schedule(kind=ScheduleKind.RECONCILE_PRODUCT_DEPLOYMENTS)

    assert schedule.workflow_name == "ReconcileProductDeploymentsWorkflow"
    assert schedule.interval_seconds == 2 * 60


def test_the_scan_is_active_by_default():
    """Read-only against the clusters; held, the products stay invisible."""
    assert ScheduleKind.RECONCILE_PRODUCT_DEPLOYMENTS in PHASE_3A_ACTIVE_KINDS
    assert ScheduleKind.RECONCILE_PRODUCT_DEPLOYMENTS in resolve_active_kinds(None)


def test_the_scheduled_workflow_and_its_activity_are_served():
    wanted = get_schedule(kind=ScheduleKind.RECONCILE_PRODUCT_DEPLOYMENTS).workflow_name

    assert wanted in {w.__temporal_workflow_definition.name for w in WORKFLOWS}
    assert reconcile_product_deployments in ACTIVITIES


def test_the_activity_runs_the_sweep(monkeypatch):
    calls: list[bool] = []

    def sweep():
        calls.append(True)
        return 3

    monkeypatch.setattr("astrolift_clusters.product_deployments.scan_product_deployments", sweep)

    assert _reconcile_product_deployments_sync() == 3
    assert calls == [True]


class _Driver:
    def list_namespaces(self, cluster, *, label_selector):
        from _sdk.cluster import NamespaceState

        return [
            NamespaceState(
                name="junohub",
                labels=WORKBENCH_LABELS,
                annotations=WORKBENCH_ANNOTATIONS,
                phase="Active",
                uid="5d1e",
            )
        ]

    def list_workload_health(self, cluster, *, namespaces=None):
        from _sdk.cluster import RunningImage, WorkloadHealth

        return [
            WorkloadHealth(
                namespace="junohub",
                name="hub",
                desired_replicas=1,
                ready_replicas=1,
                restart_count_24h=0,
                last_image_deployed_at="",
                running_images=(
                    RunningImage(
                        image="ghcr.io/calliopeai/junohub:0.1.60",
                        image_id="ghcr.io/calliopeai/junohub@sha256:0b1d",
                    ),
                ),
            )
        ]


def _world():
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
    plugin = ProviderPlugin.objects.create(
        name="k8s_native",
        slug=f"k8s-{uuid.uuid4().hex[:6]}",
        capabilities_manifest={},
        config_schema={},
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, cluster


def _recorded(org, cluster):
    from astrolift_clusters.models import ProductDeployment
    from astrolift_clusters.product_deployments import PRODUCT_DEPLOYMENT_CHANGED
    from astrolift_operations.models import Event

    rows = list(ProductDeployment.objects.filter(cluster=cluster).values("kind", "health", "running_images"))
    events = [
        e.payload["changes"]
        for e in Event.objects.filter(event_type=PRODUCT_DEPLOYMENT_CHANGED, organization=org)
    ]
    return rows, events


@pytest.mark.django_db(transaction=True)
async def test_a_real_tick_records_the_products_a_managed_cluster_runs(temporal_env, monkeypatch):
    from core import cluster_management

    org, cluster = await sync_to_async(_world)()
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: _Driver())

    async with temporal_worker(
        temporal_env,
        workflows=[ReconcileProductDeploymentsWorkflow],
        activities=[reconcile_product_deployments],
    ):
        result = await asyncio.wait_for(
            temporal_env.client.execute_workflow(
                ReconcileProductDeploymentsWorkflow.run,
                id=f"product-scan-{uuid.uuid4()}",
                task_queue="astrolift-test",
            ),
            60,
        )

    assert result.ok
    assert result.message == "scanned 1 cluster(s) for product deployments"
    rows, events = await sync_to_async(_recorded)(org, cluster)
    assert rows == [{"kind": "workbench", "health": "healthy", "running_images": {"hub": ["sha256:0b1d"]}}]
    assert events == [["appeared"]]
