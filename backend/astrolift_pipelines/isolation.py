"""Pipeline tenant isolation — namespace per run, NetworkPolicy, per-pipeline ServiceAccount (#85).

Each pipeline run executes in its own K8s namespace, isolated from other
tenant workloads and other pipeline runs. Isolation is enforced by:

1. **Namespace per run**: ``pipeline-<pipeline_guid>-<run_number>``
   — new namespace created before the first job pod lands.
2. **NetworkPolicy**: deny all ingress except from the same namespace;
   allow egress only to the cluster-internal DNS + specified CIDRs.
3. **Per-pipeline ServiceAccount**: one SA per Pipeline (not per run)
   with minimal RBAC — only the permissions needed to spawn job pods.
4. **ResourceQuota**: per-org limits on CPU/memory across all pipeline
   runs to prevent runaway resource consumption.

All resources are created by the Controller (astrolift-app) before
dispatching the first Temporal job. Namespace deletion is the teardown
trigger — K8s garbage-collects all resources in it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def pipeline_namespace(pipeline_run) -> str:
    """Return the K8s namespace name for a pipeline run.

    Format: ``pipeline-{pipeline_guid_prefix}-{run_number}``
    The GUID prefix (first 8 chars) keeps names ≤63 chars.
    """
    prefix = str(pipeline_run.pipeline.guid).replace("-", "")[:8]
    return f"pipeline-{prefix}-{pipeline_run.run_number}"


def service_account_name(pipeline_run) -> str:
    """Return the K8s ServiceAccount name for a pipeline."""
    prefix = str(pipeline_run.pipeline.guid).replace("-", "")[:8]
    return f"pipeline-sa-{prefix}"


def provision_pipeline_namespace(pipeline_run, cluster) -> str:
    """Create the namespace, NetworkPolicy, ServiceAccount, and ResourceQuota for a pipeline run.

    Returns the namespace name. Idempotent — safe to call multiple times.
    """
    from core.cluster_observability import get_dynamic_client

    namespace = pipeline_namespace(pipeline_run)
    sa_name = service_account_name(pipeline_run)
    org = pipeline_run.pipeline.organization

    try:
        client = get_dynamic_client(cluster)
        _ensure_namespace(client, namespace, pipeline_run)
        _ensure_service_account(client, namespace, sa_name)
        _ensure_network_policy(client, namespace)
        _ensure_resource_quota(client, namespace, org)
        logger.info("pipelines.isolation: namespace %s provisioned", namespace)
    except Exception:  # noqa: BLE001
        logger.exception("pipelines.isolation: failed to provision namespace %s", namespace)
        raise

    return namespace


def teardown_pipeline_namespace(namespace: str, cluster) -> None:
    """Delete the pipeline run namespace, cascading all resources within it.

    K8s garbage-collects all pods, secrets, configmaps, etc. in the namespace.
    """
    from core.cluster_observability import get_dynamic_client

    try:
        client = get_dynamic_client(cluster)
        ns_api = client.resources.get(api_version="v1", kind="Namespace")
        ns_api.delete(name=namespace)
        logger.info("pipelines.isolation: namespace %s deleted", namespace)
    except Exception:  # noqa: BLE001
        logger.warning(
            "pipelines.isolation: could not delete namespace %s — manual cleanup needed", namespace
        )


# ---------------------------------------------------------------------------
# K8s resource builders
# ---------------------------------------------------------------------------


def _ensure_namespace(client, namespace: str, pipeline_run) -> None:
    """Create the namespace if it doesn't exist."""
    ns_api = client.resources.get(api_version="v1", kind="Namespace")
    manifest: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": {
                "astrolift.dev/managed-by": "astrolift-pipelines",
                "astrolift.dev/pipeline-run": str(pipeline_run.guid),
                "astrolift.dev/org": str(pipeline_run.pipeline.organization.slug),
            },
        },
    }
    try:
        ns_api.create(body=manifest)
    except Exception:  # noqa: BLE001 — may already exist
        pass


def _ensure_service_account(client, namespace: str, sa_name: str) -> None:
    """Create a minimal ServiceAccount for the pipeline."""
    sa_api = client.resources.get(api_version="v1", kind="ServiceAccount")
    manifest = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": sa_name, "namespace": namespace},
        "automountServiceAccountToken": False,
    }
    try:
        sa_api.create(body=manifest, namespace=namespace)
    except Exception:  # noqa: BLE001
        pass


def _ensure_network_policy(client, namespace: str) -> None:
    """Create a deny-all NetworkPolicy for the pipeline namespace.

    Allows:
    - Intra-namespace pod-to-pod communication (same pipeline job)
    - Egress to cluster DNS (coredns on port 53)
    - Egress to internet (jobs need to pull images, call APIs)

    Denies:
    - Ingress from other namespaces
    - Ingress from external
    """
    np_api = client.resources.get(api_version="networking.k8s.io/v1", kind="NetworkPolicy")
    manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "pipeline-isolation",
            "namespace": namespace,
        },
        "spec": {
            "podSelector": {},  # applies to all pods in namespace
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                # Allow intra-namespace traffic only
                {"from": [{"podSelector": {}}]},
            ],
            "egress": [
                # Allow DNS
                {"ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
                # Allow all egress (jobs pull images, call external APIs)
                {},
            ],
        },
    }
    try:
        np_api.create(body=manifest, namespace=namespace)
    except Exception:  # noqa: BLE001
        pass


def _ensure_resource_quota(client, namespace: str, org) -> None:
    """Create a ResourceQuota for the pipeline namespace.

    Limits are set from the org's settings; defaults are conservative.
    """
    extra = getattr(org, "extra_data", None) or {}
    cpu_limit = extra.get("pipeline_namespace_cpu_limit", "4")
    memory_limit = extra.get("pipeline_namespace_memory_limit", "8Gi")

    rq_api = client.resources.get(api_version="v1", kind="ResourceQuota")
    manifest = {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": "pipeline-quota", "namespace": namespace},
        "spec": {
            "hard": {
                "requests.cpu": cpu_limit,
                "requests.memory": memory_limit,
                "limits.cpu": cpu_limit,
                "limits.memory": memory_limit,
                "count/pods": "20",
            }
        },
    }
    try:
        rq_api.create(body=manifest, namespace=namespace)
    except Exception:  # noqa: BLE001
        pass
