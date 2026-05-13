"""
Emit-all workflow planning policy (#22, spec 07 §11).

When the config repo is lost or corrupted, ``EmitAllWorkflow``
walks the platform DB and re-emits every active deployment's
manifests. This module is the planning + path-layout layer the
workflow uses; the rendering itself reuses the existing
``render_manifests`` activity.

Pure-Python:

* **Repo path layout** — ``<path_prefix>/<app>/<env>/manifests.yaml``.
  Both ArgoCD and Flux track per-directory.
* **Idempotency** — emit-all run twice on the same DB state must
  produce byte-identical repo contents. Caller orders deployments
  deterministically (we sort by (cluster, app, env)) and groups
  per-app/env so retries are stable.
* **Per-cluster scoping** — only deployments whose cluster is in
  GitOps mode (argocd / flux / hybrid) appear in the plan;
  direct_api clusters skip emit-all (their state lives in the
  apiserver, not in any repo).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from enum import StrEnum


class DeliveryMode(StrEnum):
    """Mirrors astrolift_clusters.delivery.DeliveryMode for callers
    that don't want to import the strategy module."""

    DIRECT_API = "direct_api"
    GITOPS_ARGOCD = "gitops_argocd"
    GITOPS_FLUX = "gitops_flux"
    HYBRID = "hybrid"


GITOPS_MODES: frozenset[DeliveryMode] = frozenset(
    {
        DeliveryMode.GITOPS_ARGOCD,
        DeliveryMode.GITOPS_FLUX,
        DeliveryMode.HYBRID,
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class ActiveDeployment:
    """Minimum projection of a Deployment row the planner needs."""

    deployment_id: int
    cluster_id: int
    cluster_slug: str
    cluster_path_prefix: str
    cluster_delivery_mode: DeliveryMode
    app_slug: str
    env_slug: str


@dataclasses.dataclass(frozen=True, slots=True)
class EmitTask:
    """One file the workflow renders + writes."""

    deployment_id: int
    cluster_slug: str
    repo_path: str  # 'clusters/prod-us-east/api/prod/manifests.yaml'


def repo_path_for(
    *,
    path_prefix: str,
    app_slug: str,
    env_slug: str,
    filename: str = "manifests.yaml",
) -> str:
    """Compose the repo path. ``path_prefix`` is the cluster's
    GitOps config root (``GitOpsConfig.path_prefix`` from #2).
    Trailing slashes on the prefix are normalised."""
    prefix = path_prefix.rstrip("/")
    return f"{prefix}/{app_slug}/{env_slug}/{filename}"


def plan_emit_all(
    deployments: Iterable[ActiveDeployment],
) -> tuple[EmitTask, ...]:
    """Build the deterministic emit task list.

    Filters out direct_api deployments (they're not represented
    in any repo). Sorts by (cluster_slug, app_slug, env_slug) so
    re-runs on the same DB state produce byte-identical commits.

    Idempotent: same input → same output, in the same order.
    """
    active_gitops: list[ActiveDeployment] = []
    for d in deployments:
        if d.cluster_delivery_mode in GITOPS_MODES:
            active_gitops.append(d)

    active_gitops.sort(key=lambda d: (d.cluster_slug, d.app_slug, d.env_slug, d.deployment_id))

    # Pick the most recent deployment per (cluster, app, env) — partial
    # rollouts can leave multiple active rows for one key, and the
    # highest deployment_id wins (last-write-wins).
    by_key: dict[tuple[str, str, str], ActiveDeployment] = {}
    for d in active_gitops:
        key = (d.cluster_slug, d.app_slug, d.env_slug)
        prev = by_key.get(key)
        if prev is None or d.deployment_id > prev.deployment_id:
            by_key[key] = d

    out_unsorted: list[EmitTask] = []
    for key in sorted(by_key.keys()):
        d = by_key[key]
        out_unsorted.append(
            EmitTask(
                deployment_id=d.deployment_id,
                cluster_slug=d.cluster_slug,
                repo_path=repo_path_for(
                    path_prefix=d.cluster_path_prefix,
                    app_slug=d.app_slug,
                    env_slug=d.env_slug,
                ),
            )
        )
    return tuple(out_unsorted)


def commits_per_cluster(
    tasks: Sequence[EmitTask],
) -> dict[str, tuple[EmitTask, ...]]:
    """Group emit tasks by cluster_slug for the commit phase. The
    workflow can either commit per-cluster (one commit per cluster
    is the spec's recommended default) or roll up into a single
    bulk commit; this returns the per-cluster grouping for the
    common case."""
    out: dict[str, list[EmitTask]] = {}
    for t in tasks:
        out.setdefault(t.cluster_slug, []).append(t)
    return {slug: tuple(tasks) for slug, tasks in out.items()}
