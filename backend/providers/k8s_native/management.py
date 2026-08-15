"""Bring-into-management driver path for the k8s_native plugin (#316).

This module owns the canonical body for ``probe_capabilities`` +
``bring_into_management``. EKS / GKE / AKS subclass
``K8sNativeClusterDriver`` and override only the auth-mint step
(``observability.build_api_client`` already routes ``exec_plugin``
auth back to the per-cloud subclass), so the RBAC apply, capability
probe, and preflight Job bodies stay here.

The k8s-client SDK import is lazy so unit tests in non-k8s plugins
don't pay for the import. Tests inject a ``ManagementBackend``
implementation that records what the driver tried to apply / list /
poll without touching a real apiserver.

Why a backend Protocol instead of monkey-patching the kubernetes SDK
directly: the kubernetes Python client surface for CRD listing,
namespaced pod listing, server-side apply via DynamicClient, and Job
status polling spans three different API groups + two SDK versions
between EKS/GKE/AKS. Wrapping all of that in a single Protocol lets
tests pin behaviour at the verb level instead of mocking
``CustomObjectsApi.list_cluster_custom_object`` etc.
"""

from __future__ import annotations

import logging
import random
import re
import string
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from _sdk.cluster import ClusterContext, JobStatus, ManagementReport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _sdk.cluster import ClusterAuth

log = logging.getLogger("k8s_native.management")


# ---- Constants ----------------------------------------------------


ASTROLIFT_NAMESPACE = "astrolift-system"
"""Single shared namespace for platform-side resources in tenant
clusters. The SA, preflight Jobs, and any future platform-owned
workloads land here so a tenant can locate everything Astrolift
manages with one ``kubectl get all -n astrolift-system``."""

PLATFORM_SA = "astrolift-control-plane"
PLATFORM_CLUSTER_ROLE = "astrolift-control-plane"
PLATFORM_CLUSTER_ROLE_BINDING = "astrolift-control-plane"

PREFLIGHT_TIMEOUT_SECONDS = 600
"""Cap on how long the workflow waits for the preflight Job. The
Job itself declares ``activeDeadlineSeconds=PREFLIGHT_TIMEOUT_SECONDS``
so the kubelet kills it past the deadline; this is the polling cap on
top.

Set to 10 minutes to comfortably cover Fargate-backed EKS clusters:
the very first pod on a fresh Fargate-only cluster can pay a 30-90s
cold-start while Fargate provisions a node before the kubelet
schedules the container, plus image pull (10-30s) and any first-time
pod-security / IRSA admission delays. A 10-min cap still fails fast
enough on genuine misconfig (PodSecurity rejection, no node capacity,
networking blackhole) — those produce errors well before 10 minutes."""

PREFLIGHT_IMAGE = "nginxinc/nginx-unprivileged:1.27-alpine"
"""Distroless-ish, well-known, runs as non-root by default — fits
the PodSecurity ``restricted`` profile without extra config so the
Job lands in clusters that ship the v1.25+ default policy."""


# ---- Capability probe shapes --------------------------------------


_DEFAULT_CAPABILITIES: dict[str, Any] = {
    "kubernetes_version": "",
    "installed_crds": [],
    "managed_service_operators": {},
    "operator_versions": {},
    "cert_manager": {
        "installed": False,
        "version": None,
        "default_issuer": None,
    },
    "ingress": {
        "class": None,
        "installed": False,
        "controller_version": None,
    },
    "storage_classes": [],
    "external_dns": {
        "installed": False,
        "provider": None,
    },
    "service_mesh": {
        "kind": None,
        "installed": False,
    },
    "metrics_server": False,
    "prometheus": False,
}


# ---- Pluggable backend --------------------------------------------


class ManagementBackend(Protocol):
    """Verb-level surface a ``K8sNativeClusterDriver`` calls into to
    do real work on a cluster.

    Tests inject a fake; the live implementation talks to a real
    kubernetes ApiClient. Subclasses for managed clouds (EKS/GKE/AKS)
    reuse the same backend because the operations are cloud-neutral
    — only auth differs, and that's already handled inside
    ``build_api_client``.
    """

    def apply_manifest(
        self,
        *,
        auth: ClusterAuth,
        manifest: dict[str, Any],
    ) -> str:
        """Server-side apply ``manifest`` to the cluster. Returns one
        of ``created`` / ``updated`` / ``unchanged``. Raises on auth /
        permission / network errors."""

    def list_cluster_crds(self, *, auth: ClusterAuth) -> list[str]:
        """Return CRD names (e.g. ``certificates.cert-manager.io``).
        Empty list when the apiserver responds but reports no CRDs."""

    def get_server_version(self, *, auth: ClusterAuth) -> str:
        """Return the live Kubernetes version (for example ``1.30.7``)."""

    def list_namespaced_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        label_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return pod summaries (``{"name", "labels", "image"}``) in
        ``namespace``. Empty list when the namespace doesn't exist."""

    def list_cluster_pods(self, *, auth: ClusterAuth) -> list[dict[str, Any]]:
        """Return pod summaries across namespaces for configurable operators."""

    def list_storage_classes(self, *, auth: ClusterAuth) -> list[str]:
        """Return storage class names. Empty list when none defined."""

    def run_preflight_job(
        self,
        *,
        auth: ClusterAuth,
        job_manifest: dict[str, Any],
        timeout_seconds: int,
    ) -> tuple[bool, str]:
        """Apply ``job_manifest``, poll until the Job reaches a
        terminal state, return ``(success, message)``. Success means
        ``status.succeeded >= 1``; failure means ``status.failed >= 1``
        or the timeout elapsed.

        The driver creates a uniquely-named Job per run so re-creating
        is straightforward — implementations don't have to dedupe."""

    def read_job_status(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        job_name: str,
    ) -> JobStatus:
        """Read one Job's ``status`` sub-resource. Read-only — never
        creates, patches, or deletes. Raises on auth / network errors and
        lets a not-found (404) propagate; the reconciler catches both and
        treats them as "can't determine, leave the row as-is"."""


# ---- Manifest builders --------------------------------------------


def _namespace_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": ASTROLIFT_NAMESPACE,
            "labels": {"astrolift.io/managed-by": "astrolift-control-plane"},
        },
    }


def _service_account_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": PLATFORM_SA,
            "namespace": ASTROLIFT_NAMESPACE,
            "labels": {"astrolift.io/managed-by": "astrolift-control-plane"},
        },
    }


def _cluster_role_manifest() -> dict[str, Any]:
    verbs = ["get", "list", "watch", "create", "update", "patch", "delete"]
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {
            "name": PLATFORM_CLUSTER_ROLE,
            "labels": {"astrolift.io/managed-by": "astrolift-control-plane"},
        },
        "rules": [
            {
                "apiGroups": ["apps"],
                "resources": ["deployments", "replicasets", "statefulsets", "daemonsets"],
                "verbs": list(verbs),
            },
            {
                "apiGroups": [""],
                "resources": [
                    "pods",
                    "services",
                    "endpoints",
                    "configmaps",
                    "secrets",
                    "persistentvolumeclaims",
                    "serviceaccounts",
                    "namespaces",
                ],
                "verbs": list(verbs),
            },
            {
                "apiGroups": ["networking.k8s.io"],
                "resources": ["ingresses", "networkpolicies"],
                "verbs": list(verbs),
            },
            {
                "apiGroups": ["batch"],
                "resources": ["jobs", "cronjobs"],
                "verbs": list(verbs),
            },
            {
                "apiGroups": ["rbac.authorization.k8s.io"],
                "resources": ["roles", "rolebindings"],
                "verbs": list(verbs),
            },
        ],
    }


def _cluster_role_binding_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {
            "name": PLATFORM_CLUSTER_ROLE_BINDING,
            "labels": {"astrolift.io/managed-by": "astrolift-control-plane"},
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": PLATFORM_SA,
                "namespace": ASTROLIFT_NAMESPACE,
            }
        ],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": PLATFORM_CLUSTER_ROLE,
        },
    }


def platform_rbac_manifests() -> list[dict[str, Any]]:
    """Ordered list of platform-RBAC manifests for the apply step.

    Ordering matters: the Namespace MUST land before the
    ServiceAccount that targets it, and the ClusterRole MUST land
    before the binding that references it.
    """
    return [
        _namespace_manifest(),
        _service_account_manifest(),
        _cluster_role_manifest(),
        _cluster_role_binding_manifest(),
    ]


def _preflight_job_manifest(*, name: str) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": ASTROLIFT_NAMESPACE,
            "labels": {
                "astrolift.io/managed-by": "astrolift-control-plane",
                "astrolift.io/preflight": "true",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": PREFLIGHT_TIMEOUT_SECONDS,
            "template": {
                "metadata": {
                    "labels": {
                        "astrolift.io/managed-by": "astrolift-control-plane",
                        "astrolift.io/preflight": "true",
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "probe",
                            "image": PREFLIGHT_IMAGE,
                            "command": [
                                "sh",
                                "-c",
                                "echo astrolift preflight ok && sleep 5",
                            ],
                            "imagePullPolicy": "IfNotPresent",
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "runAsNonRoot": True,
                                "capabilities": {"drop": ["ALL"]},
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                        }
                    ],
                },
            },
        },
    }


def _preflight_job_name() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"astrolift-preflight-{suffix}"


# ---- Probe --------------------------------------------------------


_INGRESS_NAMESPACE_HINTS: tuple[tuple[str, str], ...] = (
    # (namespace, ingress class slug). First match wins.
    ("ingress-nginx", "nginx"),
    ("kube-system", "alb"),  # AWS ALB controller often lives here
    ("aws-load-balancer-controller", "alb"),
    ("istio-system", "istio"),
    ("contour", "contour"),
    ("traefik", "traefik"),
    ("haproxy-ingress", "haproxy"),
)


_SERVICE_MESH_CRDS: tuple[tuple[str, str], ...] = (
    ("serviceentries.networking.istio.io", "istio"),
    ("virtualservices.networking.istio.io", "istio"),
    ("serviceprofiles.linkerd.io", "linkerd"),
)


def _classify_cert_manager(crds: Sequence[str], pods: Sequence[dict[str, Any]]) -> dict[str, Any]:
    installed = "certificates.cert-manager.io" in crds
    if not installed:
        return {"installed": False, "version": None, "default_issuer": None}
    version = None
    for pod in pods:
        image = str(pod.get("image", ""))
        # cert-manager images carry the chart version after the colon
        if "cert-manager" in image and ":" in image:
            version = image.rsplit(":", 1)[-1]
            break
    return {
        "installed": True,
        "version": version,
        # ``default_issuer`` is operator-configured per-cluster — we
        # can't infer it from CRDs alone. Surface ``None`` so the UI
        # nudges the operator to set one; a follow-on probe could
        # list ClusterIssuer objects and pick the one labelled
        # ``astrolift.io/default=true``.
        "default_issuer": None,
    }


def _classify_ingress(
    *,
    declared_class: str,
    pods_by_namespace: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    for namespace, slug in _INGRESS_NAMESPACE_HINTS:
        pods = pods_by_namespace.get(namespace, [])
        if not pods:
            continue
        version = None
        for pod in pods:
            image = str(pod.get("image", ""))
            if ":" in image and ("ingress" in image or "controller" in image or "alb" in image):
                version = image.rsplit(":", 1)[-1]
                break
        return {
            "class": slug,
            "installed": True,
            "controller_version": version,
        }
    if declared_class:
        # Operator told us the class but we can't see the controller —
        # leave installed=False so the UI flags the mismatch.
        return {
            "class": declared_class,
            "installed": False,
            "controller_version": None,
        }
    return {"class": None, "installed": False, "controller_version": None}


def _classify_service_mesh(crds: Sequence[str]) -> dict[str, Any]:
    for crd, kind in _SERVICE_MESH_CRDS:
        if crd in crds:
            return {"kind": kind, "installed": True}
    return {"kind": None, "installed": False}


def _classify_external_dns(pods: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not pods:
        return {"installed": False, "provider": None}
    provider = None
    for pod in pods:
        labels = pod.get("labels", {}) or {}
        provider_label = labels.get("astrolift.io/dns-provider") or labels.get("app.kubernetes.io/instance")
        if provider_label:
            provider = str(provider_label)
            break
    return {"installed": True, "provider": provider}


def _classify_metrics_and_prom(
    pods_by_namespace: dict[str, list[dict[str, Any]]],
) -> tuple[bool, bool]:
    """Detect ``metrics-server`` + Prometheus presence via pods in
    ``kube-system`` / common observability namespaces."""
    metrics = False
    prometheus = False
    for pod in pods_by_namespace.get("kube-system", []):
        labels = pod.get("labels", {}) or {}
        name = str(pod.get("name", ""))
        if "metrics-server" in name or labels.get("k8s-app") == "metrics-server":
            metrics = True
    for ns in ("monitoring", "prometheus", "observability", "kube-prometheus-stack"):
        pods = pods_by_namespace.get(ns, [])
        for pod in pods:
            labels = pod.get("labels", {}) or {}
            if labels.get("app.kubernetes.io/name") in {"prometheus", "kube-prometheus"}:
                prometheus = True
                break
            if "prometheus" in str(pod.get("name", "")):
                prometheus = True
                break
        if prometheus:
            break
    return metrics, prometheus


# Namespaces the probe inspects. Kept here as a constant so subclasses
# can extend it without re-implementing the probe body.
PROBE_NAMESPACES: tuple[str, ...] = (
    # Platform-managed namespace: Flux installs all bootstrap components
    # here when the operator uses the UI bootstrap recipe.
    "astrolift-system",
    "cert-manager",
    "ingress-nginx",
    "kube-system",
    "external-dns",
    "istio-system",
    "linkerd",
    "monitoring",
    "prometheus",
    "observability",
    "kube-prometheus-stack",
    "aws-load-balancer-controller",
    "contour",
    "traefik",
    "haproxy-ingress",
    # Managed-service operators. Their CRDs are the authoritative presence
    # signal; pods provide the release/chart version for compatibility gates.
    "cnpg-system",
    "redis-operator",
    "pxc-operator",
    "psmdb-operator",
    "kafka",
    "strimzi-system",
    "rabbitmq-system",
    "rook-ceph",
    "opensearch-operator-system",
    "argo",
    "kserve",
)


_OPERATOR_NAMESPACES: dict[str, tuple[str, ...]] = {
    "cnpg": ("cnpg-system",),
    "redis-operator": ("redis-operator",),
    "percona-xtradb-cluster": ("pxc-operator",),
    "psmdb-operator": ("psmdb-operator",),
    "strimzi-cluster-operator": ("kafka", "strimzi-system"),
    "rabbitmq-cluster-operator": ("rabbitmq-system",),
    "rook-ceph-operator": ("rook-ceph",),
    "opensearch-operator": ("opensearch-operator-system",),
    "argo-workflows": ("argo", "*"),
    "kserve": ("kserve", "*"),
}

_OPERATOR_POD_TOKENS: dict[str, tuple[str, ...]] = {
    "cnpg": ("cnpg", "cloudnative-pg"),
    "redis-operator": ("redis-operator",),
    "percona-xtradb-cluster": ("pxc-operator", "percona-xtradb"),
    "psmdb-operator": ("psmdb-operator", "percona-server-mongodb"),
    "strimzi-cluster-operator": ("strimzi",),
    "rabbitmq-cluster-operator": ("rabbitmq",),
    "rook-ceph-operator": ("rook-ceph",),
    "opensearch-operator": ("opensearch-operator",),
    "argo-workflows": ("workflow-controller",),
    "kserve": ("kserve-controller-manager", "kserve-controller"),
}


def _pod_matches_operator(pod: dict[str, Any], operator_id: str) -> bool:
    labels = pod.get("labels", {}) or {}
    searchable = " ".join(
        [
            str(pod.get("name", "")),
            str(pod.get("image", "")),
            *(str(value) for value in labels.values()),
        ]
    ).lower()
    return any(token in searchable for token in _OPERATOR_POD_TOKENS.get(operator_id, (operator_id,)))


def _operator_version(pods: Sequence[dict[str, Any]]) -> str:
    """Prefer Helm/app release labels over container image tags.

    An operator image can carry an application alpha tag while the chart is
    the supported release we gate on (OpenSearch is a concrete example).
    """
    for pod in pods:
        labels = pod.get("labels", {}) or {}
        for key in ("helm.sh/chart", "app.kubernetes.io/version"):
            value = str(labels.get(key, "") or "")
            if re.search(r"(?<![0-9])\d+\.\d+\.\d+(?![0-9])", value):
                return value
    for pod in pods:
        image = str(pod.get("image", "") or "")
        if re.search(r"(?<![0-9])\d+\.\d+\.\d+(?![0-9])", image):
            return image.rsplit(":", 1)[-1]
    return ""


def _managed_service_operator_inventory(
    *,
    crds: Sequence[str],
    pods_by_namespace: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    from k8s_native.preflight import REQUIREMENTS

    crd_set = set(crds)
    platform_pods = pods_by_namespace.get(ASTROLIFT_NAMESPACE, [])
    inventory: dict[str, dict[str, Any]] = {}
    versions: dict[str, str] = {}
    requirements_by_operator = {req.operator_id: req for req in REQUIREMENTS.values() if req.required_crds}
    for operator_id, requirement in sorted(requirements_by_operator.items()):
        pods = [pod for pod in platform_pods if _pod_matches_operator(pod, operator_id)]
        for namespace in _OPERATOR_NAMESPACES.get(operator_id, ()):
            pods.extend(pod for pod in pods_by_namespace.get(namespace, []) if _pod_matches_operator(pod, operator_id))
        missing_crds = sorted(set(requirement.required_crds) - crd_set)
        version = _operator_version(pods)
        if version:
            versions[operator_id] = version
        inventory[operator_id] = {
            "installed": not missing_crds,
            "version": version or None,
            "required_crds": list(requirement.required_crds),
            "missing_crds": missing_crds,
        }
    return inventory, versions


def probe_cluster_capabilities(
    *,
    backend: ManagementBackend,
    cluster: ClusterContext,
) -> dict[str, Any]:
    """Run the capability probe. Returns the dict the workflow
    persists onto ``TenantCluster.capabilities``.

    Defensive against missing-namespace errors: the backend's
    ``list_namespaced_pods`` returns an empty list for namespaces
    that don't exist, so the probe treats unknown namespaces as
    "feature not installed" rather than raising.
    """
    auth = cluster.to_auth()
    crds = backend.list_cluster_crds(auth=auth)
    version_probe = getattr(backend, "get_server_version", None)
    kubernetes_version = str(version_probe(auth=auth) if callable(version_probe) else "")
    pods_by_namespace: dict[str, list[dict[str, Any]]] = {}
    for ns in PROBE_NAMESPACES:
        try:
            pods_by_namespace[ns] = backend.list_namespaced_pods(auth=auth, namespace=ns)
        except Exception as exc:
            # Treat individual-namespace failures as "not installed"
            # rather than failing the whole probe. A missing namespace
            # commonly surfaces as a 404 wrapped in an SDK exception.
            log.debug("probe: skipping namespace %s: %s", ns, exc)
            pods_by_namespace[ns] = []
    cluster_pod_probe = getattr(backend, "list_cluster_pods", None)
    if callable(cluster_pod_probe):
        try:
            pods_by_namespace["*"] = cluster_pod_probe(auth=auth)
        except Exception as exc:
            log.debug("probe: cluster-wide pod discovery unavailable: %s", exc)
            pods_by_namespace["*"] = []

    storage_classes = backend.list_storage_classes(auth=auth)

    # When operators use the UI bootstrap recipe, all components land in
    # astrolift-system via Flux HelmRelease. Merge those pods into each
    # classifier's candidate list so detection works regardless of whether
    # the operator used the platform's Flux path or installed into the
    # conventional per-component namespace manually.
    platform_pods = pods_by_namespace.get("astrolift-system", [])

    cert_manager_pods = pods_by_namespace.get("cert-manager", []) + [
        p
        for p in platform_pods
        if "cert-manager" in str(p.get("name", ""))
        or p.get("labels", {}).get("app.kubernetes.io/name", "") == "cert-manager"
    ]
    cert_manager = _classify_cert_manager(crds, cert_manager_pods)

    ingress = _classify_ingress(
        declared_class=cluster.ingress_class,
        pods_by_namespace=pods_by_namespace,
    )
    service_mesh = _classify_service_mesh(crds)

    external_dns_pods = pods_by_namespace.get("external-dns", []) + [
        p
        for p in platform_pods
        if "external-dns" in str(p.get("name", ""))
        or p.get("labels", {}).get("app.kubernetes.io/name", "") == "external-dns"
    ]
    external_dns = _classify_external_dns(external_dns_pods)

    # Extend pods_by_namespace with astrolift-system pods bucketed under
    # the conventional namespace keys so _classify_metrics_and_prom can
    # find them without a signature change.
    augmented = dict(pods_by_namespace)
    augmented.setdefault("kube-system", [])
    augmented["kube-system"] = augmented["kube-system"] + [
        p
        for p in platform_pods
        if "metrics-server" in str(p.get("name", ""))
        or p.get("labels", {}).get("app.kubernetes.io/name", "") == "metrics-server"
    ]
    augmented.setdefault("kube-prometheus-stack", [])
    augmented["kube-prometheus-stack"] = augmented["kube-prometheus-stack"] + [
        p
        for p in platform_pods
        if "prometheus" in str(p.get("name", ""))
        or p.get("labels", {}).get("app.kubernetes.io/name", "") in {"prometheus", "kube-prometheus-stack"}
    ]
    metrics_server, prometheus = _classify_metrics_and_prom(augmented)
    operator_inventory, operator_versions = _managed_service_operator_inventory(
        crds=crds,
        pods_by_namespace=pods_by_namespace,
    )

    # Auto-discover Prometheus pod IP for direct VPC-native queries from
    # the ECS control plane. Stored in capabilities["prometheus_endpoint"]
    # so the metrics resolver can use it without manual operator config.
    # Refreshed on every capability probe — operators just run
    # refreshClusterManagement if the pod restarts with a new IP.
    # Note: EKS Fargate pod IPs are VPC-native (real ENI IPs) and routable
    # from ECS. K8s Service ClusterIPs are virtual/iptables-only and are
    # NOT routable from outside the cluster.
    #
    # Scan the same set of namespaces that _classify_metrics_and_prom uses
    # so endpoint discovery works regardless of whether the operator installed
    # Prometheus into kube-prometheus-stack, monitoring, observability, etc.
    prometheus_endpoint: str | None = None
    if prometheus:
        _prom_scan_namespaces = (
            "kube-prometheus-stack",
            "monitoring",
            "prometheus",
            "observability",
            "astrolift-system",
        )
        for _ns in _prom_scan_namespaces:
            for pod in pods_by_namespace.get(_ns, []):
                ip = pod.get("pod_ip")
                # Use ONLY the app.kubernetes.io/name label — NOT a name
                # substring match.  kube-prometheus-stack names all pods with
                # "prometheus" in their names (alertmanager, operator, etc.),
                # so a name check selects whichever pod the K8s API returns
                # first, which is typically NOT the actual server pod.
                if ip and pod.get("labels", {}).get("app.kubernetes.io/name") in {
                    "prometheus",
                    "kube-prometheus",
                }:
                    prometheus_endpoint = f"http://{ip}:9090"
                    break
            if prometheus_endpoint:
                break

    return {
        "kubernetes_version": kubernetes_version,
        "installed_crds": sorted(set(crds)),
        "managed_service_operators": operator_inventory,
        "operator_versions": operator_versions,
        "cert_manager": cert_manager,
        "ingress": ingress,
        "storage_classes": list(storage_classes),
        "external_dns": external_dns,
        "service_mesh": service_mesh,
        "metrics_server": metrics_server,
        "prometheus": prometheus,
        "prometheus_endpoint": prometheus_endpoint,
    }


def read_cluster_job_status(
    *,
    backend: ManagementBackend,
    cluster: ClusterContext,
    namespace: str,
    job_name: str,
) -> JobStatus:
    """Read one batch/v1 Job's status through ``backend``.

    Mirrors :func:`probe_cluster_capabilities` — the driver hands us a
    (per-cloud auth-resolved) ``ClusterContext`` and the shared backend;
    we project it onto ``ClusterAuth`` and issue the single read-only
    ``read_namespaced_job_status`` call. No mutation of any kind.
    """
    return backend.read_job_status(
        auth=cluster.to_auth(),
        namespace=namespace,
        job_name=job_name,
    )


# ---- Bring-into-management orchestration --------------------------


def _empty_capabilities() -> dict[str, Any]:
    """Fresh copy of the zero-state shape; used when probe fails so
    the report still carries a well-shaped dict for the UI."""
    import copy

    return copy.deepcopy(_DEFAULT_CAPABILITIES)


@dataclass(frozen=True)
class _RbacOutcome:
    success: bool
    messages: list[str] = field(default_factory=list)
    error: str | None = None


def _apply_platform_rbac(
    *,
    backend: ManagementBackend,
    cluster: ClusterContext,
) -> _RbacOutcome:
    """Apply each manifest in ``platform_rbac_manifests()`` in order.
    Any single-manifest failure short-circuits the run — partial RBAC
    is worse than no RBAC because the workflow can't tell if the
    failure was the SA or the binding."""
    auth = cluster.to_auth()
    messages: list[str] = []
    for manifest in platform_rbac_manifests():
        kind = manifest.get("kind", "")
        name = manifest.get("metadata", {}).get("name", "")
        ref = f"{kind}/{name}"
        try:
            outcome = backend.apply_manifest(auth=auth, manifest=manifest)
        except Exception as exc:
            return _RbacOutcome(
                success=False,
                messages=messages,
                error=f"RBAC apply failed at {ref}: {exc}",
            )
        messages.append(f"RBAC {ref} {outcome}")
    return _RbacOutcome(success=True, messages=messages)


def run_bring_into_management(
    *,
    backend: ManagementBackend,
    cluster: ClusterContext,
    run_preflight: bool = True,
) -> ManagementReport:
    """Canonical orchestrator. Subclasses with cloud-specific auth
    quirks construct a backend whose ``apply_manifest`` / probe calls
    route through a per-cloud ApiClient; the orchestration body stays
    here so behaviour is shared.

    Order:
      1. apply platform RBAC (4 manifests; ordering matters)
      2. probe capabilities (read-only)
      3. optionally run the preflight Job

    On any failure the function returns ``success=False`` with the
    failure reason in ``error``; capability data collected before the
    failure still ships back so the UI can show what was learned.
    """
    rbac = _apply_platform_rbac(backend=backend, cluster=cluster)
    messages: list[str] = list(rbac.messages)
    if not rbac.success:
        return ManagementReport(
            success=False,
            rbac_applied=False,
            capabilities=_empty_capabilities(),
            preflight_status="skipped",
            error=rbac.error,
            messages=messages,
        )

    try:
        capabilities = probe_cluster_capabilities(backend=backend, cluster=cluster)
    except Exception as exc:
        return ManagementReport(
            success=False,
            rbac_applied=True,
            capabilities=_empty_capabilities(),
            preflight_status="skipped",
            error=f"capability probe failed: {exc}",
            messages=messages,
        )
    messages.append(
        f"probed capabilities: cert_manager.installed={capabilities['cert_manager']['installed']}, "
        f"ingress.class={capabilities['ingress']['class']}"
    )

    if not run_preflight:
        return ManagementReport(
            success=True,
            rbac_applied=True,
            capabilities=capabilities,
            preflight_status="skipped",
            error=None,
            messages=messages,
        )

    job_name = _preflight_job_name()
    job_manifest = _preflight_job_manifest(name=job_name)
    started = time.monotonic()
    try:
        ok, message = backend.run_preflight_job(
            auth=cluster.to_auth(),
            job_manifest=job_manifest,
            timeout_seconds=PREFLIGHT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return ManagementReport(
            success=False,
            rbac_applied=True,
            capabilities=capabilities,
            preflight_status="failed",
            error=f"preflight Job {job_name} raised: {exc}",
            messages=messages,
        )
    elapsed = int(time.monotonic() - started)
    messages.append(f"preflight Job {job_name} {'passed' if ok else 'failed'} ({message}; {elapsed}s)")
    if not ok:
        return ManagementReport(
            success=False,
            rbac_applied=True,
            capabilities=capabilities,
            preflight_status="failed",
            error=message or f"preflight Job {job_name} did not succeed within {PREFLIGHT_TIMEOUT_SECONDS}s",
            messages=messages,
        )

    return ManagementReport(
        success=True,
        rbac_applied=True,
        capabilities=capabilities,
        preflight_status="passed",
        error=None,
        messages=messages,
    )


# ---- Live backend (kubernetes SDK) --------------------------------


@dataclass
class LiveManagementBackend:
    """Default backend — talks to the kubernetes apiserver via the
    same ``build_api_client`` factory the observability path uses.

    Construction is cheap; the ApiClient itself is built lazily on
    the first call so a driver instantiated for read-only ops never
    pays the auth handshake.
    """

    def apply_manifest(
        self,
        *,
        auth: ClusterAuth,
        manifest: dict[str, Any],
    ) -> str:
        try:
            from kubernetes import dynamic
            from kubernetes.client import api_client as _api_client_mod
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "kubernetes python client is not installed; install astrolift-providers[k8s]",
            ) from exc
        from k8s_native.observability import build_api_client

        del _api_client_mod  # kept for the import-time guard above

        api_client = build_api_client(auth)
        dyn = dynamic.DynamicClient(api_client)
        api_version = manifest.get("apiVersion", "v1")
        kind = manifest.get("kind", "")
        name = manifest.get("metadata", {}).get("name", "")
        namespace = manifest.get("metadata", {}).get("namespace")
        resource = dyn.resources.get(api_version=api_version, kind=kind)
        try:
            existing = resource.get(name=name, namespace=namespace)
        except Exception:
            existing = None
        if existing is None:
            resource.create(body=manifest, namespace=namespace)
            return "created"
        resource.server_side_apply(
            body=manifest,
            namespace=namespace,
            field_manager="astrolift-control-plane",
            force_conflicts=True,
        )
        return "updated"

    def list_cluster_crds(self, *, auth: ClusterAuth) -> list[str]:
        # #765 — route through the low-level call_api wrapper so a
        # kubernetes-client minor bump can't silently break auth
        # dispatch on this hot path.  Same observable behavior as the
        # high-level ``ApiextensionsV1Api.list_custom_resource_definition``,
        # but bypasses any high-level method drift.
        from k8s_native._api_client_helpers import list_cluster_crd_names
        from k8s_native.observability import build_api_client

        return list_cluster_crd_names(build_api_client(auth))

    def get_server_version(self, *, auth: ClusterAuth) -> str:
        from k8s_native._api_client_helpers import get_server_version_dict
        from k8s_native.observability import build_api_client

        payload = get_server_version_dict(build_api_client(auth))
        git_version = str(payload.get("gitVersion", "") or "").lstrip("v")
        if git_version:
            return git_version
        major = re.sub(r"\D", "", str(payload.get("major", "") or ""))
        minor = re.sub(r"\D", "", str(payload.get("minor", "") or ""))
        return ".".join(part for part in (major, minor) if part)

    def list_namespaced_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        label_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        # #765 — low-level wrapper for cross-version-stable auth.
        from k8s_native._api_client_helpers import list_namespaced_pod_dicts
        from k8s_native.observability import build_api_client

        try:
            pods = list_namespaced_pod_dicts(
                build_api_client(auth),
                namespace=namespace,
                label_selector=label_selector,
            )
        except Exception:
            # Probe path expects "no pods" rather than an exception for
            # missing-namespace + transient errors. Matches the prior
            # high-level method's behavior.
            return []

        out: list[dict[str, Any]] = []
        for pod in pods:
            metadata = pod.get("metadata") or {}
            spec = pod.get("spec") or {}
            containers = spec.get("containers") or []
            image = containers[0].get("image", "") if containers else ""
            out.append(
                {
                    "name": metadata.get("name", ""),
                    "labels": dict(metadata.get("labels") or {}),
                    "image": image,
                    "pod_ip": (pod.get("status") or {}).get("podIP"),
                }
            )
        return out

    def list_cluster_pods(self, *, auth: ClusterAuth) -> list[dict[str, Any]]:
        from k8s_native._api_client_helpers import list_cluster_pod_dicts
        from k8s_native.observability import build_api_client

        try:
            pods = list_cluster_pod_dicts(build_api_client(auth))
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for pod in pods:
            metadata = pod.get("metadata") or {}
            spec = pod.get("spec") or {}
            containers = spec.get("containers") or []
            image = containers[0].get("image", "") if containers else ""
            out.append(
                {
                    "name": metadata.get("name", ""),
                    "labels": dict(metadata.get("labels") or {}),
                    "image": image,
                    "pod_ip": (pod.get("status") or {}).get("podIP"),
                    "namespace": metadata.get("namespace", ""),
                },
            )
        return out

    def list_storage_classes(self, *, auth: ClusterAuth) -> list[str]:
        try:
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("kubernetes python client is not installed") from exc
        from k8s_native.observability import build_api_client

        api_client = build_api_client(auth)
        storage_v1 = k8s_client.StorageV1Api(api_client)
        resp = storage_v1.list_storage_class(timeout_seconds=10)
        return sorted(item.metadata.name for item in (resp.items or []))

    def run_preflight_job(
        self,
        *,
        auth: ClusterAuth,
        job_manifest: dict[str, Any],
        timeout_seconds: int,
    ) -> tuple[bool, str]:
        try:
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("kubernetes python client is not installed") from exc
        from k8s_native.observability import build_api_client

        api_client = build_api_client(auth)
        batch_v1 = k8s_client.BatchV1Api(api_client)

        namespace = job_manifest["metadata"]["namespace"]
        name = job_manifest["metadata"]["name"]
        batch_v1.create_namespaced_job(namespace=namespace, body=job_manifest)
        deadline = time.monotonic() + timeout_seconds
        last_message = ""
        while time.monotonic() < deadline:
            try:
                job = batch_v1.read_namespaced_job_status(name=name, namespace=namespace)
            except Exception as exc:
                last_message = f"status read failed: {exc}"
                time.sleep(2)
                continue
            status = getattr(job, "status", None)
            if status is None:
                time.sleep(2)
                continue
            succeeded = int(getattr(status, "succeeded", 0) or 0)
            failed = int(getattr(status, "failed", 0) or 0)
            conds = getattr(status, "conditions", None) or []
            for c in conds:
                msg = getattr(c, "message", "") or ""
                if msg:
                    last_message = msg
            if succeeded >= 1:
                return True, last_message or "preflight Job completed"
            if failed >= 1:
                return False, last_message or "preflight Job failed"
            time.sleep(2)
        return False, last_message or f"preflight Job did not complete within {timeout_seconds}s"

    def read_job_status(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        job_name: str,
    ) -> JobStatus:
        """Single read-only ``read_namespaced_job_status`` call — the same
        BatchV1Api verb ``run_preflight_job`` polls with, projected onto the
        SDK's :class:`JobStatus`. Never creates/patches/deletes."""
        try:
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("kubernetes python client is not installed") from exc
        from k8s_native.observability import build_api_client

        api_client = build_api_client(auth)
        batch_v1 = k8s_client.BatchV1Api(api_client)
        job = batch_v1.read_namespaced_job_status(name=job_name, namespace=namespace)

        status = getattr(job, "status", None)
        if status is None:
            return JobStatus()
        conds = getattr(status, "conditions", None) or []
        messages = tuple(msg for c in conds if (msg := (getattr(c, "message", "") or "").strip()))
        return JobStatus(
            active=int(getattr(status, "active", 0) or 0),
            succeeded=int(getattr(status, "succeeded", 0) or 0),
            failed=int(getattr(status, "failed", 0) or 0),
            start_time=getattr(status, "start_time", None),
            completion_time=getattr(status, "completion_time", None),
            conditions=messages,
        )


def default_management_backend() -> ManagementBackend:
    """Return the live backend when the kubernetes SDK is importable.

    Mirrors the observability ``default_log_backend()`` shape so the
    driver constructor only sees ``None`` vs. ``ManagementBackend`` —
    we never return a stub for management because there's nothing
    useful to do without a real apiserver. If the SDK is missing,
    the live backend raises on its first method call; that's a
    clearer error than silently no-oping the workflow.
    """
    return LiveManagementBackend()
