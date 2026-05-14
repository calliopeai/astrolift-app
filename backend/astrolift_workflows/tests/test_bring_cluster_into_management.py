"""Tests for BringClusterIntoManagementWorkflow activities (#316).

The activity layer is the contract — each activity's sync body is the
unit of durable work, and the workflow body is straight-line
sequencing with try/except per activity. Pinning the activity sync
bodies against a real Postgres + record-only fake ``ManagementBackend``
covers every behaviour the workflow can exhibit:

  * mark_managing / mark_managed / mark_error: lifecycle writes
  * verify_reachability: pre-write auth check + soft-delete / inactive
    refusal
  * apply_platform_rbac: ordered manifest apply via the canonical
    k8s_native body; 403 / network failures bubble as RuntimeError
  * probe_capabilities: capability snapshot persists onto the row
  * run_preflight_job: success returns a message, failure raises

The workflow-orchestrator side (try/except + _fail() → mark_error) is
covered by reading the workflow source and reusing the same activity
contracts; we don't spin up the Temporal worker because the
transactional-test fixture rolls back rows the worker's connection
can't then see, and a full ``transaction=True`` setup is too heavy
for the surface under test (the orchestration logic is simple
sequential dispatch — there's no scheduler-level behaviour worth
isolating here).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_workflows.activities.cluster_management import (
    _apply_platform_rbac_sync,
    _mark_error_sync,
    _mark_managed_sync,
    _mark_managing_sync,
    _probe_capabilities_sync,
    _run_preflight_job_sync,
    _verify_reachability_sync,
)
from core.cluster_management import (
    reset_management_backend_for_tests,
    set_management_backend_for_tests,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the User -> Profile -> OpenSearch indexing chain that
    fires on every Organization/User create."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


# ---- Fixtures + fake backend --------------------------------------


@dataclass
class _FakeManagementBackend:
    """Mirrors ``k8s_native.management.FakeManagementBackend`` used in
    the providers test suite — deterministic responses keyed off the
    fixture-staged CRDs / pods / preflight result."""

    crds: list[str] = field(default_factory=list)
    pods_by_namespace: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    storage_classes: list[str] = field(default_factory=list)
    apply_raises_on: set[str] = field(default_factory=set)
    crd_listing_raises: bool = False
    preflight_result: tuple[bool, str] = (True, "preflight Job completed")

    applied: list[dict[str, Any]] = field(default_factory=list)
    preflight_invocations: list[dict[str, Any]] = field(default_factory=list)

    def apply_manifest(self, *, auth, manifest):
        kind = manifest.get("kind", "")
        name = manifest.get("metadata", {}).get("name", "")
        ref = f"{kind}/{name}"
        if ref in self.apply_raises_on:
            raise PermissionError(f"forbidden: {ref}")
        self.applied.append(manifest)
        seen = sum(
            1 for m in self.applied if m.get("kind") == kind and m.get("metadata", {}).get("name") == name
        )
        return "created" if seen == 1 else "updated"

    def list_cluster_crds(self, *, auth):
        if self.crd_listing_raises:
            raise RuntimeError("apiserver unreachable")
        return list(self.crds)

    def list_namespaced_pods(self, *, auth, namespace, label_selector=None):
        return list(self.pods_by_namespace.get(namespace, []))

    def list_storage_classes(self, *, auth):
        return list(self.storage_classes)

    def run_preflight_job(self, *, auth, job_manifest, timeout_seconds):
        self.preflight_invocations.append(
            {
                "namespace": job_manifest["metadata"]["namespace"],
                "name": job_manifest["metadata"]["name"],
            }
        )
        return self.preflight_result


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def provider_plugin():
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug="k8s_native",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, provider_plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{uuid.uuid4().hex[:6]}",
        name="dev-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.REGISTERED.value,
    )


@pytest.fixture
def fake_backend():
    backend = _FakeManagementBackend(
        crds=["certificates.cert-manager.io"],
        pods_by_namespace={
            "cert-manager": [
                {
                    "name": "cert-manager-1",
                    "labels": {"app.kubernetes.io/name": "cert-manager"},
                    "image": "cert-manager-controller:v1.16.2",
                }
            ]
        },
        storage_classes=["gp3"],
    )
    set_management_backend_for_tests(backend)
    yield backend
    reset_management_backend_for_tests()


# ---- Activity-body tests (sync core) ------------------------------


def test_mark_managing_sync_flips_lifecycle_and_clears_error(cluster):
    cluster.last_management_error = "previous failure"
    cluster.save(update_fields=["last_management_error"])
    _mark_managing_sync(cluster.pk)
    cluster.refresh_from_db()
    assert cluster.lifecycle == TenantCluster.Lifecycle.MANAGING.value
    assert cluster.last_management_error == ""


def test_mark_managing_sync_is_idempotent_on_already_managing_row(cluster):
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGING.value
    cluster.save(update_fields=["lifecycle"])
    before = cluster.updated_at
    _mark_managing_sync(cluster.pk)
    cluster.refresh_from_db()
    assert cluster.lifecycle == TenantCluster.Lifecycle.MANAGING.value
    # No write -> updated_at unchanged
    assert cluster.updated_at == before


def test_verify_reachability_raises_on_deleted_cluster(cluster, fake_backend):
    cluster.soft_delete()
    with pytest.raises(RuntimeError, match="unregistered"):
        _verify_reachability_sync(cluster.pk)


def test_verify_reachability_raises_on_inactive_cluster(cluster, fake_backend):
    cluster.is_active = False
    cluster.save(update_fields=["is_active"])
    with pytest.raises(RuntimeError, match="inactive"):
        _verify_reachability_sync(cluster.pk)


def test_verify_reachability_propagates_apiserver_failure(cluster, fake_backend):
    fake_backend.crd_listing_raises = True
    with pytest.raises(RuntimeError, match="apiserver unreachable"):
        _verify_reachability_sync(cluster.pk)


def test_verify_reachability_ok_on_healthy_cluster(cluster, fake_backend):
    _verify_reachability_sync(cluster.pk)  # no raise


def test_apply_platform_rbac_sync_returns_messages_and_applies_in_order(cluster, fake_backend):
    messages = _apply_platform_rbac_sync(cluster.pk)
    assert any("Namespace/astrolift-system" in m for m in messages)
    assert [m["kind"] for m in fake_backend.applied] == [
        "Namespace",
        "ServiceAccount",
        "ClusterRole",
        "ClusterRoleBinding",
    ]
    # No preflight Job at this step
    assert fake_backend.preflight_invocations == []


def test_apply_platform_rbac_sync_raises_on_403(cluster, fake_backend):
    fake_backend.apply_raises_on = {"ClusterRole/astrolift-control-plane"}
    with pytest.raises(RuntimeError, match="ClusterRole/astrolift-control-plane"):
        _apply_platform_rbac_sync(cluster.pk)


def test_probe_capabilities_sync_persists_to_row(cluster, fake_backend):
    capabilities = _probe_capabilities_sync(cluster.pk)
    cluster.refresh_from_db()
    assert cluster.capabilities["cert_manager"]["installed"] is True
    assert cluster.capabilities["cert_manager"]["version"] == "v1.16.2"
    assert cluster.capabilities_probed_at is not None
    assert capabilities == cluster.capabilities


def test_run_preflight_job_sync_success_returns_message(cluster, fake_backend):
    message = _run_preflight_job_sync(cluster.pk)
    assert "preflight" in message.lower()
    assert len(fake_backend.preflight_invocations) == 1


def test_run_preflight_job_sync_raises_on_failure(cluster, fake_backend):
    fake_backend.preflight_result = (False, "preflight Job failed: ImagePullBackOff")
    with pytest.raises(RuntimeError, match="ImagePullBackOff"):
        _run_preflight_job_sync(cluster.pk)


def test_mark_managed_sync_sets_managed_at_and_clears_error(cluster):
    cluster.last_management_error = "stale failure"
    cluster.save(update_fields=["last_management_error"])
    _mark_managed_sync(cluster.pk)
    cluster.refresh_from_db()
    assert cluster.lifecycle == TenantCluster.Lifecycle.MANAGED.value
    assert cluster.managed_at is not None
    assert cluster.last_management_error == ""


def test_mark_error_sync_truncates_long_messages(cluster):
    long_message = "x" * 5000
    _mark_error_sync(cluster.pk, long_message)
    cluster.refresh_from_db()
    assert cluster.lifecycle == TenantCluster.Lifecycle.ERROR.value
    assert len(cluster.last_management_error) == 4000


def test_mark_error_sync_handles_empty_message(cluster):
    _mark_error_sync(cluster.pk, "")
    cluster.refresh_from_db()
    assert cluster.lifecycle == TenantCluster.Lifecycle.ERROR.value
    assert cluster.last_management_error == "unknown error"


# ---- Idempotent re-run on managed cluster ------------------------


def test_apply_platform_rbac_sync_reconciles_on_already_managed_row(cluster, fake_backend):
    """Refresh path: row is already managed, run RBAC apply again.
    Should succeed without raising and apply all four manifests
    (which surface as ``updated`` from the fake backend's
    seen-count logic — but the orchestrator's success gate doesn't
    look at create-vs-update)."""
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGED.value
    cluster.save(update_fields=["lifecycle"])
    messages = _apply_platform_rbac_sync(cluster.pk)
    assert len(fake_backend.applied) == 4
    assert messages  # non-empty


def test_probe_capabilities_sync_overwrites_stale_capabilities(cluster, fake_backend):
    """A refresh after capabilities change (e.g. cert-manager upgraded)
    should overwrite the JSONField rather than merging."""
    cluster.capabilities = {"cert_manager": {"installed": False, "version": "v0.0.0"}}
    cluster.save(update_fields=["capabilities"])
    _probe_capabilities_sync(cluster.pk)
    cluster.refresh_from_db()
    assert cluster.capabilities["cert_manager"]["version"] == "v1.16.2"
