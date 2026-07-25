"""Managed-model agent auto-wire for the K8s Job spawner.

When an :class:`~astrolift_agents.models.AgentEnvironmentSpec` has the
``managed_model`` switch on, its task pod talks to the CLUSTER'S
cloud-native model provider (AWS→Bedrock, GCP→Vertex) via a
workload-identity ServiceAccount instead of an ``ANTHROPIC_API_KEY``.
Today an agent task pod dies with "ANTHROPIC_API_KEY is not set" because
nothing injects a model credential and the pod runs under the ``default``
ServiceAccount with no cloud identity.

This module is the dispatch-side seam between the cluster driver (which
owns the cloud-specific identity mint + model env) and the spawner (which
renders the Job). It:

  1. resolves the task's cluster ``ClusterDriver`` + ``provider_config``;
  2. calls the driver's idempotent ``ensure_agent_model_identity`` to mint
     (or reuse) the cloud identity and get back its identifier (an IAM
     role ARN on AWS);
  3. computes the provider's model env via ``agent_model_env``;
  4. builds an annotated ServiceAccount manifest the spawner applies
     (idempotent server-side apply) so the pod-identity webhook injects
     credentials.

A provider that doesn't implement the managed-model path — or a cluster
missing the required wiring — raises :class:`ManagedModelError`, which the
spawner surfaces on the task as one actionable error rather than a silent
skip that would crash-loop the pod.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("astrolift_dispatch.agent_model")

# Stable ServiceAccount name the managed-model pod runs under. Deliberately
# NOT ``default`` — the annotated SA is what binds the cloud identity, and
# reusing ``default`` would attach the model role to every pod in the
# namespace.
AGENT_MODEL_SERVICE_ACCOUNT = "astrolift-agent-model"

# Annotation the EKS pod-identity webhook reads to inject IRSA credentials.
# This is the AWS/IRSA key; the only cloud whose driver currently mints an
# identity (and therefore reaches SA-manifest building) is EKS. When GKE
# Workload Identity / AKS federated identity land, the annotation key must
# come from the driver alongside the returned identifier.
AGENT_MODEL_ROLE_ANNOTATION = "eks.amazonaws.com/role-arn"

# Manifest labels — greppable + consistent with the per-task secret's
# ``astrolift.dev/managed-by`` (see ``agent_secrets._MANAGED_BY``).
_MANAGED_BY = "astrolift-agents"


class ManagedModelError(Exception):
    """Managed-model wiring for a task couldn't be resolved.

    Collapses every failure mode into one surfaced message: the provider
    doesn't support managed model, the cluster driver can't be built, the
    identity mint failed, or the model env couldn't be computed. The
    spawner returns it as the task's spawn error.
    """


@dataclass(frozen=True)
class ManagedModelWiring:
    """Resolved managed-model wiring for one task spawn.

    ``env`` is the provider's model env as container ``env`` entries
    (``[{"name", "value"}]``) to merge BEFORE the spec's own env so an
    explicit spec override wins. ``service_account`` is the SA name the Job
    pod runs under; ``service_account_manifest`` is the annotated SA object
    the spawner applies (idempotent SSA) before the Job.
    """

    env: list[dict[str, str]]
    service_account: str
    service_account_manifest: dict[str, Any]


def build_service_account_manifest(
    *,
    namespace: str,
    service_account: str,
    role_arn: str,
) -> dict[str, Any]:
    """Annotated ServiceAccount manifest for the managed-model pod.

    Server-side applied by the spawner (idempotent — re-dispatch converges
    the annotation to the current role ARN). The ``eks.amazonaws.com/role-arn``
    annotation is what the EKS pod-identity webhook reads to inject
    credentials for the assumed IAM role.
    """
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": service_account,
            "namespace": namespace,
            "labels": {
                "astrolift.dev/managed-by": _MANAGED_BY,
                "astrolift.dev/component": "agent-model",
            },
            "annotations": {AGENT_MODEL_ROLE_ANNOTATION: role_arn},
        },
    }


def resolve_managed_model_wiring(*, cluster, namespace: str) -> ManagedModelWiring:
    """Resolve the cloud identity + model env for a managed-model task.

    Mints (or reuses) the cluster's model workload identity and computes
    the provider's model env, returning the pieces the spawner threads into
    the Job (env + serviceAccountName) plus the annotated SA manifest to
    apply. Raises :class:`ManagedModelError` on any failure so the spawn
    fails fast with an actionable message.

    ``cluster`` is the task's :class:`~astrolift_clusters.models.TenantCluster`
    (its ``provider_config`` + ``region`` carry the driver overrides). The
    driver resolution imports :func:`core.cluster_management._driver_for_cluster`
    at call time so a test-installed driver override is honoured.
    """
    # Import at call time: the spawner path monkeypatches
    # ``core.cluster_management._driver_for_cluster`` in tests, and a
    # module-level import would bind the original before the patch.
    from astrolift_dispatch.agent_secrets import env_var_entries
    from core.cluster_management import ClusterManagementError, _driver_for_cluster

    try:
        driver = _driver_for_cluster(cluster)
    except ClusterManagementError as exc:
        raise ManagedModelError(f"managed model: {exc}") from exc

    provider_config: dict[str, Any] = dict(getattr(cluster, "provider_config", None) or {})
    region = str(getattr(cluster, "region", "") or provider_config.get("region", "") or "")

    try:
        role_ref = driver.ensure_agent_model_identity(
            namespace=namespace,
            service_account=AGENT_MODEL_SERVICE_ACCOUNT,
            provider_config=provider_config,
        )
    except ManagedModelError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any driver failure as one message
        raise ManagedModelError(
            f"managed model: could not provision the model workload identity: {exc}",
        ) from exc

    if not role_ref:
        raise ManagedModelError(
            "managed model: the cluster driver returned no workload-identity reference",
        )

    try:
        env_map = driver.agent_model_env(region=region, provider_config=provider_config)
    except Exception as exc:  # noqa: BLE001 — surface any driver failure as one message
        raise ManagedModelError(
            f"managed model: could not resolve the provider model env: {exc}",
        ) from exc

    sa_manifest = build_service_account_manifest(
        namespace=namespace,
        service_account=AGENT_MODEL_SERVICE_ACCOUNT,
        role_arn=str(role_ref),
    )
    return ManagedModelWiring(
        env=env_var_entries(env_map),
        service_account=AGENT_MODEL_SERVICE_ACCOUNT,
        service_account_manifest=sa_manifest,
    )
