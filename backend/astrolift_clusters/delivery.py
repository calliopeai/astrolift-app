"""
Delivery strategy dispatch (#2, spec 07 §2).

Each TenantCluster declares how the platform pushes rendered
manifests to it: ``direct_api`` (apiserver SSA), ``gitops_argocd``,
``gitops_flux``, or ``hybrid``. The deploy workflow asks this
module 'what should happen with these objects on this cluster?'
and gets back a typed ``DeliveryPlan`` it acts on.

Pure-Python strategy pattern. The actual driver calls (apiserver
apply, git commit + ArgoCD sync trigger) live elsewhere — this
module is the dispatcher.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from enum import StrEnum


class DeliveryMode(StrEnum):
    """Mirrors the model's TextChoices so non-Django callers don't
    need to import the model class."""

    DIRECT_API = "direct_api"
    GITOPS_ARGOCD = "gitops_argocd"
    GITOPS_FLUX = "gitops_flux"
    HYBRID = "hybrid"


# Object kinds that always go through GitOps in HYBRID mode (CRDs,
# cluster-scoped RBAC, namespace-level RBAC). Workload-y objects
# (Deployment, Service, ConfigMap, Ingress, ...) take the fast path.
_HYBRID_GITOPS_KINDS: frozenset[str] = frozenset(
    {
        "CustomResourceDefinition",
        "ClusterRole",
        "ClusterRoleBinding",
        "Role",
        "RoleBinding",
        "Namespace",
        "ResourceQuota",
        "NetworkPolicy",
    }
)


class DeliveryConfigError(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class GitOpsConfig:
    """Per-cluster GitOps settings the strategy needs."""

    repo_url: str
    branch: str
    path_prefix: str
    """Sub-tree under which this cluster's manifests live, e.g.
    ``clusters/prod-us-east/``. Multiple clusters can share one
    repo via different prefixes."""

    def __post_init__(self) -> None:
        if not self.repo_url:
            raise DeliveryConfigError("GitOps repo_url is required")
        if not self.branch:
            raise DeliveryConfigError("GitOps branch is required")


@dataclasses.dataclass(frozen=True, slots=True)
class DirectApplyAction:
    """The deploy workflow runs ``ClusterDriver.apply(objects)``."""

    objects: tuple[Mapping[str, object], ...]


@dataclasses.dataclass(frozen=True, slots=True)
class GitOpsCommitAction:
    """The deploy workflow commits ``objects`` to ``config`` and
    waits for the syncer to converge."""

    objects: tuple[Mapping[str, object], ...]
    config: GitOpsConfig
    syncer: str  # 'argocd' | 'flux' (informational; UI shows status)


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryPlan:
    """The dispatcher's output: a list of actions to run in order."""

    direct: DirectApplyAction | None = None
    gitops: GitOpsCommitAction | None = None


# ---- the dispatcher -------------------------------------------------


def plan_delivery(
    *,
    mode: DeliveryMode,
    objects: Iterable[Mapping[str, object]],
    gitops_config: GitOpsConfig | None = None,
) -> DeliveryPlan:
    """Dispatch ``objects`` per the cluster's delivery mode.

    DIRECT_API: every object → apiserver apply.
    GITOPS_ARGOCD / GITOPS_FLUX: every object → git commit +
        syncer trigger. ``gitops_config`` required.
    HYBRID: cluster-scoped RBAC + CRDs + namespaces → git;
        everything else → apiserver apply. ``gitops_config``
        required only when the object set actually contains
        gitops-eligible kinds.
    """
    objs = tuple(objects)

    if mode == DeliveryMode.DIRECT_API:
        return DeliveryPlan(direct=DirectApplyAction(objects=objs))

    if mode in (DeliveryMode.GITOPS_ARGOCD, DeliveryMode.GITOPS_FLUX):
        if gitops_config is None:
            raise DeliveryConfigError(f"mode {mode.value!r} requires gitops_config")
        syncer = "argocd" if mode == DeliveryMode.GITOPS_ARGOCD else "flux"
        return DeliveryPlan(
            gitops=GitOpsCommitAction(
                objects=objs,
                config=gitops_config,
                syncer=syncer,
            )
        )

    if mode == DeliveryMode.HYBRID:
        gitops_objs = tuple(o for o in objs if _kind_of(o) in _HYBRID_GITOPS_KINDS)
        direct_objs = tuple(o for o in objs if _kind_of(o) not in _HYBRID_GITOPS_KINDS)
        plan = DeliveryPlan()
        if direct_objs:
            plan = dataclasses.replace(
                plan,
                direct=DirectApplyAction(objects=direct_objs),
            )
        if gitops_objs:
            if gitops_config is None:
                raise DeliveryConfigError("HYBRID mode with platform-level objects requires gitops_config")
            # Hybrid uses ArgoCD by default; operators can override
            # via delivery_config['hybrid_syncer'] in the model.
            plan = dataclasses.replace(
                plan,
                gitops=GitOpsCommitAction(
                    objects=gitops_objs,
                    config=gitops_config,
                    syncer="argocd",
                ),
            )
        return plan

    raise DeliveryConfigError(f"unsupported delivery mode {mode!r}")


def _kind_of(obj: Mapping[str, object]) -> str:
    """Pull the k8s ``kind`` field. Defaults to empty string when
    missing — caller's manifests should always set it; we don't
    assume."""
    kind = obj.get("kind")
    return str(kind) if isinstance(kind, str) else ""
