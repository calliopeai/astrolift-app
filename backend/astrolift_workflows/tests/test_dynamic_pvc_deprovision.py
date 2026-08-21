from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _sdk.cluster import DeleteResult, StorageClassInfo
from _sdk.managed_service import DeprovisionResult

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.filesystem_bindings import binding_resource_name, storage_consumer_key
from astrolift_services.models import (
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceVolumeBinding,
)
from astrolift_workflows.activities.managed_service_lifecycle import _deprovision_sync

pytestmark = pytest.mark.django_db


class _ManagedDriver:
    def __init__(self, *, config) -> None:
        self.config = config

    def deprovision(self, spec, *, delete_data=False, force_destroy=False):
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message="template retired",
        )


class _ClusterDriver:
    def __init__(self, *, reclaim_policy: str = "Delete") -> None:
        self.calls = []
        self.reclaim_policy = reclaim_policy

    def list_storage_classes(self, _cluster):
        return [
            StorageClassInfo(
                name="standard",
                is_default=True,
                provisioner="example.csi.invalid",
                reclaim_policy=self.reclaim_policy,
            ),
        ]

    def delete_manifests(self, cluster, namespace, manifests):
        self.calls.append((cluster, namespace, manifests))
        return DeleteResult(
            deleted=[f"PersistentVolumeClaim/{manifests[0]['metadata']['name']}"],
            not_found=[],
            errors=[],
        )


def _scaffold():
    org = Organization.objects.create(name="Acme PVC", slug="acme-pvc")
    team = Team.objects.create(organization=org, name="Eng PVC", slug="eng-pvc")
    project = Project.objects.create(organization=org, team=team, name="Files", slug="files-pvc")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Kubernetes",
                slug="k8s-pvc",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-pvc")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="on-prem-pvc",
        name="On Prem",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="API PVC",
        slug="api-pvc",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    service = ManagedService.objects.create(
        project=project,
        tenant_cluster=cluster,
        environment_name="shared",
        kind=ManagedService.Kind.FILESYSTEM,
        variant="storage_class_pvc",
        name="workspace",
        status=ManagedService.Status.DEPROVISIONING,
        backend_ref="filesystem/cluster-1/acme-files/workspace",
    )
    binding = ManagedServiceVolumeBinding.objects.create(
        managed_service=service,
        name="workspace",
        mount_path="/workspace",
        source_kind="dynamic_pvc",
        protocol="pvc",
        storage_class_name="standard",
        capacity="10Gi",
        access_modes=["ReadWriteOnce"],
    )
    attachment = ManagedServiceAttachment.objects.create(
        managed_service=service,
        app_environment=env,
    )
    return service, binding, attachment


def _patch_runtime(cluster_driver):
    return (
        patch("astrolift_drivers.registry.plugins.get", return_value=_ManagedDriver),
        patch("core.cluster_observability.managed_config_for", return_value={}),
        patch("core.cluster_management._driver_for_cluster", return_value=cluster_driver),
        patch(
            "core.cluster_management._context_for_cluster",
            return_value=SimpleNamespace(slug="on-prem-pvc"),
        ),
    )


def test_destructive_dynamic_pvc_deprovision_refuses_active_attachment() -> None:
    service, _binding, _attachment = _scaffold()
    cluster_driver = _ClusterDriver()
    patches = _patch_runtime(cluster_driver)

    with patches[0], patches[1], patches[2], patches[3]:
        result = _deprovision_sync(service.pk, delete_data=True, force_destroy=False)

    assert result["ok"] is False
    assert result["retryable"] is False
    assert "active attachments" in result["message"]
    assert cluster_driver.calls == []


def test_forced_dynamic_pvc_deprovision_deletes_consumer_claim() -> None:
    service, binding, _attachment = _scaffold()
    cluster_driver = _ClusterDriver()
    patches = _patch_runtime(cluster_driver)

    with patches[0], patches[1], patches[2], patches[3]:
        result = _deprovision_sync(service.pk, delete_data=True, force_destroy=True)

    expected_key = storage_consumer_key(binding, namespace="acme-pvc-api-pvc", consumer_key="unused")
    expected_name = binding_resource_name(binding, expected_key)
    assert result["ok"] is True
    assert "deleted 1 dynamic claim" in result["message"]
    assert cluster_driver.calls == [
        (
            "on-prem-pvc",
            "acme-pvc-api-pvc",
            [
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {
                        "name": expected_name,
                        "namespace": "acme-pvc-api-pvc",
                    },
                },
            ],
        ),
    ]


def test_dynamic_pvc_data_delete_refuses_retain_storage_class() -> None:
    service, _binding, attachment = _scaffold()
    attachment.soft_delete()
    cluster_driver = _ClusterDriver(reclaim_policy="Retain")
    patches = _patch_runtime(cluster_driver)

    with patches[0], patches[1], patches[2], patches[3]:
        result = _deprovision_sync(service.pk, delete_data=True, force_destroy=False)

    assert result["ok"] is False
    assert "reclaimPolicy 'Retain'" in result["message"]
    assert cluster_driver.calls == []


def test_dynamic_pvc_data_delete_refuses_missing_binding_metadata() -> None:
    service, binding, attachment = _scaffold()
    attachment.soft_delete()
    binding.delete()
    cluster_driver = _ClusterDriver()
    patches = _patch_runtime(cluster_driver)

    with patches[0], patches[1], patches[2], patches[3]:
        result = _deprovision_sync(service.pk, delete_data=True, force_destroy=False)

    assert result["ok"] is False
    assert "no active volume binding" in result["message"]
    assert cluster_driver.calls == []
