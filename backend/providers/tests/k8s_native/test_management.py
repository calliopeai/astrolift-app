"""Tests for the k8s_native bring-into-management driver path (#316).

The live kubernetes apiserver is heavy to stand up — these tests
exercise the orchestration through a fake ``ManagementBackend`` so
each behaviour is pinned at the verb level:

  - Platform RBAC manifests apply in the right order
  - The capability probe returns the expected shape from CRDs +
    pods + storage classes
  - Preflight Job success/failure flips the report's ``success``
  - ``run_preflight=False`` skips the Job (refresh path)
  - Probe handles missing namespaces (404) without failing the run
  - RBAC apply failure short-circuits and leaves capabilities empty
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from _sdk.cluster import ClusterAuth, ClusterContext, JobStatus
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig
from k8s_native.management import (
    ASTROLIFT_NAMESPACE,
    PLATFORM_CLUSTER_ROLE,
    PLATFORM_CLUSTER_ROLE_BINDING,
    PLATFORM_SA,
    platform_rbac_manifests,
    probe_cluster_capabilities,
    read_cluster_job_status,
    run_bring_into_management,
)

# ---- Fakes --------------------------------------------------------


@dataclass
class FakeManagementBackend:
    """Deterministic ``ManagementBackend`` for tests.

    Records every call + lets each fixture stage CRDs / namespace
    pods / storage classes the probe should see. The Job runner
    returns ``preflight_result`` so tests can pin both success and
    failure paths.
    """

    crds: list[str] = field(default_factory=list)
    pods_by_namespace: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    storage_classes: list[str] = field(default_factory=list)
    kubernetes_version: str = "1.30.7"
    apply_raises_on: set[str] = field(default_factory=set)
    """Set of ``Kind/name`` refs the apply call should raise on."""

    preflight_result: tuple[bool, str] = (True, "preflight Job completed")
    preflight_raises: bool = False

    job_status: JobStatus = field(default_factory=JobStatus)
    job_status_raises: bool = False

    applied: list[dict[str, Any]] = field(default_factory=list)
    preflight_invocations: list[dict[str, Any]] = field(default_factory=list)
    job_status_invocations: list[dict[str, Any]] = field(default_factory=list)

    def apply_manifest(self, *, auth: ClusterAuth, manifest: dict[str, Any]) -> str:
        kind = manifest.get("kind", "")
        name = manifest.get("metadata", {}).get("name", "")
        ref = f"{kind}/{name}"
        if ref in self.apply_raises_on:
            raise PermissionError(f"forbidden: {ref}")
        self.applied.append(manifest)
        # First time we see this manifest -> created; otherwise updated
        seen = sum(1 for m in self.applied if m.get("kind") == kind and m.get("metadata", {}).get("name") == name)
        return "created" if seen == 1 else "updated"

    def list_cluster_crds(self, *, auth: ClusterAuth) -> list[str]:
        return list(self.crds)

    def get_server_version(self, *, auth: ClusterAuth) -> str:
        return self.kubernetes_version

    def list_namespaced_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        label_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        return list(self.pods_by_namespace.get(namespace, []))

    def list_storage_classes(self, *, auth: ClusterAuth) -> list[str]:
        return list(self.storage_classes)

    def run_preflight_job(
        self,
        *,
        auth: ClusterAuth,
        job_manifest: dict[str, Any],
        timeout_seconds: int,
    ) -> tuple[bool, str]:
        if self.preflight_raises:
            raise RuntimeError("kubelet-side failure")
        self.preflight_invocations.append(
            {
                "namespace": job_manifest["metadata"]["namespace"],
                "name": job_manifest["metadata"]["name"],
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.preflight_result

    def read_job_status(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        job_name: str,
    ) -> JobStatus:
        if self.job_status_raises:
            raise RuntimeError("apiserver unreachable")
        self.job_status_invocations.append({"namespace": namespace, "job_name": job_name})
        return self.job_status


def _ctx(slug: str = "test-cluster", *, ingress_class: str = "nginx") -> ClusterContext:
    return ClusterContext(
        slug=slug,
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "fake"},
        ingress_class=ingress_class,
    )


# ---- Manifest shape tests -----------------------------------------


def test_platform_rbac_manifests_are_ordered_namespace_first():
    manifests = platform_rbac_manifests()
    assert [m["kind"] for m in manifests] == [
        "Namespace",
        "ServiceAccount",
        "ClusterRole",
        "ClusterRoleBinding",
    ]
    assert manifests[0]["metadata"]["name"] == ASTROLIFT_NAMESPACE
    assert manifests[1]["metadata"]["name"] == PLATFORM_SA
    assert manifests[1]["metadata"]["namespace"] == ASTROLIFT_NAMESPACE
    assert manifests[2]["metadata"]["name"] == PLATFORM_CLUSTER_ROLE
    assert manifests[3]["metadata"]["name"] == PLATFORM_CLUSTER_ROLE_BINDING
    binding = manifests[3]
    assert binding["roleRef"]["name"] == PLATFORM_CLUSTER_ROLE
    assert binding["subjects"][0]["name"] == PLATFORM_SA
    assert binding["subjects"][0]["namespace"] == ASTROLIFT_NAMESPACE


def test_cluster_role_includes_required_verbs_on_core_resources():
    role = next(m for m in platform_rbac_manifests() if m["kind"] == "ClusterRole")
    rule_for_core = next(r for r in role["rules"] if r["apiGroups"] == [""])
    assert {"get", "list", "watch", "create", "update", "patch", "delete"}.issubset(set(rule_for_core["verbs"]))
    # ConfigMaps + Secrets + Ingresses-via-networking-group all
    # appear so the platform can run a tenant deploy.
    assert "configmaps" in rule_for_core["resources"]
    assert "secrets" in rule_for_core["resources"]
    networking = next(r for r in role["rules"] if r["apiGroups"] == ["networking.k8s.io"])
    assert "ingresses" in networking["resources"]


# ---- Probe tests --------------------------------------------------


def test_probe_returns_default_shape_on_empty_cluster():
    backend = FakeManagementBackend()
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["cert_manager"] == {"installed": False, "version": None, "default_issuer": None}
    assert caps["ingress"]["installed"] is False
    assert caps["storage_classes"] == []
    assert caps["service_mesh"] == {"kind": None, "installed": False}
    assert caps["external_dns"] == {"installed": False, "provider": None}
    assert caps["metrics_server"] is False
    assert caps["prometheus"] is False
    assert caps["kubernetes_version"] == "1.30.7"
    assert caps["installed_crds"] == []


def test_probe_inventories_opensearch_crds_and_chart_version():
    crds = [
        "opensearchclusters.opensearch.org",
        "opensearchusers.opensearch.org",
        "opensearchroles.opensearch.org",
        "opensearchuserrolebindings.opensearch.org",
    ]
    backend = FakeManagementBackend(
        crds=crds,
        pods_by_namespace={
            "opensearch-operator-system": [
                {
                    "name": "opensearch-operator-controller-manager-abc",
                    "labels": {
                        "app.kubernetes.io/name": "opensearch-operator",
                        "helm.sh/chart": "opensearch-operator-3.0.2",
                    },
                    "image": "opensearchproject/opensearch-operator:3.0.0-alpha1",
                }
            ]
        },
    )

    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())

    assert caps["installed_crds"] == sorted(crds)
    assert caps["operator_versions"]["opensearch-operator"] == "opensearch-operator-3.0.2"
    assert caps["managed_service_operators"]["opensearch-operator"] == {
        "installed": True,
        "version": "opensearch-operator-3.0.2",
        "required_crds": crds,
        "missing_crds": [],
    }


def test_platform_namespace_operator_version_ignores_unrelated_charts():
    required = [
        "opensearchclusters.opensearch.org",
        "opensearchusers.opensearch.org",
        "opensearchroles.opensearch.org",
        "opensearchuserrolebindings.opensearch.org",
    ]
    backend = FakeManagementBackend(
        crds=required,
        pods_by_namespace={
            "astrolift-system": [
                {
                    "name": "cert-manager-abc",
                    "labels": {"helm.sh/chart": "cert-manager-v1.16.2"},
                    "image": "quay.io/jetstack/cert-manager-controller:v1.16.2",
                },
                {
                    "name": "opensearch-operator-controller-manager-abc",
                    "labels": {"helm.sh/chart": "opensearch-operator-3.0.2"},
                    "image": "opensearchproject/opensearch-operator:3.0.0-alpha1",
                },
            ]
        },
    )

    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())

    assert caps["operator_versions"]["opensearch-operator"] == "opensearch-operator-3.0.2"


def test_probe_detects_cert_manager_via_crd_and_version_from_pod_image():
    backend = FakeManagementBackend(
        crds=["certificates.cert-manager.io", "clusterissuers.cert-manager.io"],
        pods_by_namespace={
            "cert-manager": [
                {
                    "name": "cert-manager-7d8b8d6c-x4r2k",
                    "labels": {"app.kubernetes.io/name": "cert-manager"},
                    "image": "quay.io/jetstack/cert-manager-controller:v1.16.2",
                }
            ]
        },
    )
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["cert_manager"]["installed"] is True
    assert caps["cert_manager"]["version"] == "v1.16.2"


def test_probe_detects_ingress_controller_in_alb_namespace():
    backend = FakeManagementBackend(
        pods_by_namespace={
            "aws-load-balancer-controller": [
                {
                    "name": "aws-load-balancer-controller-789",
                    "labels": {"app.kubernetes.io/name": "aws-load-balancer-controller"},
                    "image": "public.ecr.aws/eks/aws-load-balancer-controller:v2.7.2",
                }
            ]
        },
    )
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["ingress"]["installed"] is True
    assert caps["ingress"]["class"] == "alb"
    assert caps["ingress"]["controller_version"] == "v2.7.2"


def test_probe_flags_declared_class_without_visible_controller():
    backend = FakeManagementBackend()  # no controller pods anywhere
    caps = probe_cluster_capabilities(
        backend=backend,
        cluster=_ctx(ingress_class="nginx"),
    )
    assert caps["ingress"]["class"] == "nginx"
    assert caps["ingress"]["installed"] is False


def test_probe_detects_storage_classes():
    backend = FakeManagementBackend(storage_classes=["gp3", "io2"])
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["storage_classes"] == ["gp3", "io2"]


def test_probe_detects_istio_via_crd():
    backend = FakeManagementBackend(
        crds=["serviceentries.networking.istio.io", "virtualservices.networking.istio.io"],
    )
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["service_mesh"] == {"kind": "istio", "installed": True}


def test_probe_detects_metrics_server_via_kube_system_pod():
    backend = FakeManagementBackend(
        pods_by_namespace={
            "kube-system": [
                {
                    "name": "metrics-server-78f48c4b6f-abc",
                    "labels": {"k8s-app": "metrics-server"},
                    "image": "registry.k8s.io/metrics-server/metrics-server:v0.7.0",
                }
            ]
        },
    )
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["metrics_server"] is True


def test_probe_auto_discovers_prometheus_endpoint_from_pod_ip():
    """When Prometheus is detected and the candidate pod has a podIP,
    the probe surfaces ``prometheus_endpoint`` so the metrics resolver
    can hit the pod directly without operator-provided config."""
    backend = FakeManagementBackend(
        pods_by_namespace={
            "kube-prometheus-stack": [
                {
                    "name": "prometheus-kube-prometheus-prometheus-0",
                    "labels": {"app.kubernetes.io/name": "prometheus"},
                    "image": "quay.io/prometheus/prometheus:v2.54.0",
                    "pod_ip": "10.42.1.17",
                }
            ]
        },
    )
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["prometheus"] is True
    assert caps["prometheus_endpoint"] == "http://10.42.1.17:9090"


def test_probe_prometheus_endpoint_none_when_no_prometheus():
    backend = FakeManagementBackend()
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["prometheus"] is False
    assert caps["prometheus_endpoint"] is None


def test_probe_prometheus_endpoint_none_when_pod_ip_missing():
    """Prometheus detected but pod is still pending (no podIP yet) ->
    flag installed but leave endpoint None; the next refresh will pick
    it up once the pod has scheduled."""
    backend = FakeManagementBackend(
        pods_by_namespace={
            "monitoring": [
                {
                    "name": "prometheus-server-0",
                    "labels": {"app.kubernetes.io/name": "prometheus"},
                    "image": "quay.io/prometheus/prometheus:v2.54.0",
                    "pod_ip": None,
                }
            ]
        },
    )
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["prometheus"] is True
    assert caps["prometheus_endpoint"] is None


def test_probe_auto_discovers_prometheus_endpoint_from_platform_namespace():
    """Flux-installed stacks land in astrolift-system; the endpoint
    fallback should pick up the prometheus pod from there too."""
    backend = FakeManagementBackend(
        pods_by_namespace={
            "astrolift-system": [
                {
                    "name": "prometheus-astrolift-0",
                    "labels": {"app.kubernetes.io/name": "prometheus"},
                    "image": "quay.io/prometheus/prometheus:v2.54.0",
                    "pod_ip": "10.42.5.23",
                }
            ]
        },
    )
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    assert caps["prometheus"] is True
    assert caps["prometheus_endpoint"] == "http://10.42.5.23:9090"


def test_probe_tolerates_missing_namespaces():
    """A namespace that doesn't exist surfaces as an exception from
    the underlying SDK; the probe should swallow it and treat the
    capability as absent."""

    class _RaisingBackend(FakeManagementBackend):
        def list_namespaced_pods(self, *, auth, namespace, label_selector=None):
            if namespace == "cert-manager":
                raise RuntimeError("404 namespace not found")
            return super().list_namespaced_pods(auth=auth, namespace=namespace, label_selector=label_selector)

    backend = _RaisingBackend(crds=["certificates.cert-manager.io"])
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())
    # CRD is present so cert_manager.installed=True even though pods raised
    assert caps["cert_manager"]["installed"] is True
    # No version because the pod query failed
    assert caps["cert_manager"]["version"] is None


# ---- Orchestration tests ------------------------------------------


def test_happy_path_applies_rbac_probes_and_runs_preflight():
    backend = FakeManagementBackend(
        crds=["certificates.cert-manager.io"],
        pods_by_namespace={
            "cert-manager": [
                {
                    "name": "cert-manager-1",
                    "labels": {"app.kubernetes.io/name": "cert-manager"},
                    "image": "cert-manager-controller:v1.16.2",
                }
            ],
        },
        storage_classes=["gp3"],
    )
    report = run_bring_into_management(backend=backend, cluster=_ctx())
    assert report.success is True
    assert report.rbac_applied is True
    assert report.preflight_status == "passed"
    assert report.error is None
    # All four RBAC manifests applied in order
    assert [m["kind"] for m in backend.applied] == [
        "Namespace",
        "ServiceAccount",
        "ClusterRole",
        "ClusterRoleBinding",
    ]
    # Preflight ran exactly once in astrolift-system
    assert len(backend.preflight_invocations) == 1
    inv = backend.preflight_invocations[0]
    assert inv["namespace"] == ASTROLIFT_NAMESPACE
    assert inv["name"].startswith("astrolift-preflight-")
    # Preflight timeout: 600s (10 min) gives Fargate cold-start
    # headroom — short timeouts repeatedly fired before the pod
    # could schedule, leaving the operator with a misleading
    # "preflight failed" instead of "still warming up".
    assert inv["timeout_seconds"] == 600
    # Capabilities snapshot landed in the report
    assert report.capabilities["cert_manager"]["installed"] is True
    assert report.capabilities["storage_classes"] == ["gp3"]


def test_run_preflight_false_skips_job_for_refresh_path():
    backend = FakeManagementBackend()
    report = run_bring_into_management(backend=backend, cluster=_ctx(), run_preflight=False)
    assert report.success is True
    assert report.preflight_status == "skipped"
    assert backend.preflight_invocations == []
    # RBAC reconcile still ran
    assert len(backend.applied) == 4


def test_rbac_apply_403_short_circuits_with_error():
    backend = FakeManagementBackend(
        apply_raises_on={"ClusterRole/astrolift-control-plane"},
    )
    report = run_bring_into_management(backend=backend, cluster=_ctx())
    assert report.success is False
    assert report.rbac_applied is False
    assert report.preflight_status == "skipped"
    assert report.error is not None
    assert "ClusterRole/astrolift-control-plane" in report.error
    # The namespace + SA applied before the failure
    assert [m["kind"] for m in backend.applied] == ["Namespace", "ServiceAccount"]
    # Preflight never ran because RBAC failed
    assert backend.preflight_invocations == []
    # Capabilities is the empty shape; structure preserved for the UI
    assert report.capabilities["cert_manager"]["installed"] is False


def test_preflight_failure_flips_success_to_false_but_keeps_caps():
    backend = FakeManagementBackend(
        crds=["certificates.cert-manager.io"],
        preflight_result=(False, "preflight Job failed: ImagePullBackOff"),
    )
    report = run_bring_into_management(backend=backend, cluster=_ctx())
    assert report.success is False
    assert report.rbac_applied is True
    assert report.preflight_status == "failed"
    assert "ImagePullBackOff" in (report.error or "")
    # Capabilities probe DID run before preflight, so cert_manager
    # is recorded even on preflight failure.
    assert report.capabilities["cert_manager"]["installed"] is True


def test_preflight_backend_exception_propagates_as_failure():
    backend = FakeManagementBackend(preflight_raises=True)
    report = run_bring_into_management(backend=backend, cluster=_ctx())
    assert report.success is False
    assert report.preflight_status == "failed"
    assert "kubelet-side" in (report.error or "")


# ---- Driver-level wiring ------------------------------------------


def test_driver_delegates_probe_to_management_backend():
    backend = FakeManagementBackend(storage_classes=["fast-ssd"])
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        management_backend=backend,
    )
    caps = driver.probe_capabilities(_ctx())
    assert caps["storage_classes"] == ["fast-ssd"]


def test_driver_delegates_bring_into_management_to_management_backend():
    backend = FakeManagementBackend()
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        management_backend=backend,
    )
    report = driver.bring_into_management(_ctx())
    assert report.success is True
    assert report.rbac_applied is True
    assert len(backend.applied) == 4


def test_driver_inventories_filesystem_runtime_dependencies():
    client = MagicMock()
    client.list.return_value = [{"metadata": {"name": "nfs.csi.k8s.io"}}]
    client.get.return_value = {"metadata": {"name": "shared"}}
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        k8s_client_factory=lambda **_kwargs: client,
    )

    assert driver.list_csi_drivers("native") == ["nfs.csi.k8s.io"]
    assert driver.persistent_volume_claim_exists("native", "acme-api", "shared") is True
    assert driver.get_manifest(
        "native",
        "storage",
        "seaweed.seaweedfs.com/v1/Bucket",
        "uploads",
    ) == {"metadata": {"name": "shared"}}


# ---- read_job_status (run reconciler) -----------------------------


def test_read_cluster_job_status_projects_backend_result():
    """The orchestrator hands the backend the context's auth + the
    namespace/job and returns its JobStatus verbatim."""
    backend = FakeManagementBackend(
        job_status=JobStatus(succeeded=1, conditions=("done",)),
    )
    js = read_cluster_job_status(
        backend=backend,
        cluster=_ctx(),
        namespace="acme-app",
        job_name="nightly-manual-abc",
    )
    assert js.succeeded == 1
    assert js.conditions == ("done",)
    assert backend.job_status_invocations == [
        {"namespace": "acme-app", "job_name": "nightly-manual-abc"},
    ]


def test_driver_delegates_read_job_status_to_management_backend():
    backend = FakeManagementBackend(job_status=JobStatus(failed=1))
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        management_backend=backend,
    )
    js = driver.read_job_status(_ctx(), namespace="ns", job_name="job-1")
    assert js.failed == 1
    assert backend.job_status_invocations == [{"namespace": "ns", "job_name": "job-1"}]


def test_read_job_status_propagates_backend_error():
    """A read failure (unreachable apiserver / 404) propagates so the
    reconciler's per-run guard can treat it as 'can't determine'."""
    backend = FakeManagementBackend(job_status_raises=True)
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        management_backend=backend,
    )
    with pytest.raises(RuntimeError, match="apiserver unreachable"):
        driver.read_job_status(_ctx(), namespace="ns", job_name="job-1")
