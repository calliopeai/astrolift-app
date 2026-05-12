"""
Release promotion + deployment lineage policy (#63, spec 14 §14).

Two patterns:

* **Promote build** — re-deploy a known-good image digest from one
  env to another without rebuilding. The new Deployment's
  ``promoted_from_id`` links to the source.
* **Branch-per-env** — pushes from a specific branch route to a
  specific environment (e.g. ``staging`` → staging env, ``main`` →
  production).

Pure-Python policy. The mutation that creates the Deployment row
and the workflow that applies it both consult this module for the
'is this promotion valid?' decision and the lineage walk.

Lineage walking handles cycles defensively — a malicious or
corrupted ``promoted_from_id`` chain shouldn't be able to deadlock
the API. Cycle detection caps the walk and surfaces the corruption.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

# ---- promote build ---------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentRef:
    """Minimal projection of a Deployment row used by the policy.
    Caller flattens the model into one of these."""

    deployment_id: int
    app_id: int
    environment_id: int
    image_digest: str
    """Spec 12 §9 / #26 — every deployed image is pinned to its
    digest. Promotion copies the digest verbatim."""

    promoted_from_id: int | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class PromotionTarget:
    """The destination env for a promotion."""

    environment_id: int
    app_id: int
    requires_approval: bool


class PromotionError(ValueError):
    """The requested promotion violates policy."""


def validate_promotion(
    *,
    source: DeploymentRef,
    target: PromotionTarget,
) -> None:
    """Reject impossible promotions before creating the Deployment row.

    Rules:
      - source and target must belong to the same app (you can't
        promote one app's image into another's env).
      - cannot promote to the same environment as the source
        (no-op promotion is a misclick — surface it).
      - source must carry a digest (you can't promote an unpinned
        image; it'd be 'roughly the same' which defeats the
        whole 'byte-identical' point).
    """
    if source.app_id != target.app_id:
        raise PromotionError(f"cannot promote: source app {source.app_id} != target app " f"{target.app_id}")
    if source.environment_id == target.environment_id:
        raise PromotionError(f"cannot promote to the source environment ({source.environment_id})")
    if not source.image_digest:
        raise PromotionError(
            f"source deployment {source.deployment_id} has no image digest "
            "(cannot guarantee byte-identical promotion)"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class PromotionPlan:
    """Output of plan_promotion. Caller builds the Deployment row
    from this and runs DeployAppWorkflow with it."""

    target_environment_id: int
    app_id: int
    image_digest: str
    promoted_from_id: int
    needs_approval: bool


def plan_promotion(
    *,
    source: DeploymentRef,
    target: PromotionTarget,
) -> PromotionPlan:
    """Validate + emit the plan. Raises :class:`PromotionError` on
    invalid promotions."""
    validate_promotion(source=source, target=target)
    return PromotionPlan(
        target_environment_id=target.environment_id,
        app_id=target.app_id,
        image_digest=source.image_digest,
        promoted_from_id=source.deployment_id,
        needs_approval=target.requires_approval,
    )


# ---- branch-per-env routing ----------------------------------------


def env_for_branch(
    *,
    branch_name: str,
    branch_to_env: Mapping[str, int],
) -> int | None:
    """Resolve which environment a push to ``branch_name`` deploys to.

    ``branch_to_env`` maps exact branch names to environment IDs. A
    branch with no mapping returns None — the caller (push webhook
    handler) treats that as 'no auto-deploy for this branch'.

    Default branches:
      ``main``    → production
      ``staging`` → staging
    are conventions, not built-in — the org/app config supplies the
    map so non-conventional setups are supported.
    """
    return branch_to_env.get(branch_name)


# ---- lineage walk --------------------------------------------------


# Defensive cap on the lineage walk. Realistic chains rarely exceed
# 10 hops (build → staging → prod-canary → prod-ring1 → ... ). 100
# is several orders of magnitude beyond what's plausible; if we hit
# it, the chain is almost certainly corrupted.
MAX_LINEAGE_HOPS = 100


class LineageCycle(Exception):
    """The promoted_from_id chain has a cycle. Almost certainly
    DB corruption — surface loudly so an operator investigates."""


def walk_lineage(
    *,
    leaf: DeploymentRef,
    by_id: Mapping[int, DeploymentRef],
) -> tuple[DeploymentRef, ...]:
    """Walk back through the promotion chain, leaf-first.

    Returns the chain ordered from leaf to root. ``by_id`` is the
    caller-supplied lookup (typically a single DB query that
    pre-loaded the relevant Deployment rows).

    Raises :class:`LineageCycle` if the same deployment id appears
    twice (corrupted chain) or the walk exceeds MAX_LINEAGE_HOPS.
    A missing parent (id present in ``promoted_from_id`` but not in
    ``by_id``) terminates the walk — typical for old deployments
    pruned by retention.
    """
    chain: list[DeploymentRef] = [leaf]
    seen: set[int] = {leaf.deployment_id}
    current = leaf

    for _ in range(MAX_LINEAGE_HOPS):
        if current.promoted_from_id is None:
            return tuple(chain)
        parent = by_id.get(current.promoted_from_id)
        if parent is None:
            return tuple(chain)
        if parent.deployment_id in seen:
            raise LineageCycle(
                f"promotion lineage cycle detected at deployment "
                f"{parent.deployment_id} (already in chain)"
            )
        chain.append(parent)
        seen.add(parent.deployment_id)
        current = parent

    raise LineageCycle(f"promotion lineage exceeded {MAX_LINEAGE_HOPS} hops; " "chain is corrupt")


def root_of_lineage(
    *,
    leaf: DeploymentRef,
    by_id: Mapping[int, DeploymentRef],
) -> DeploymentRef:
    """Return the original (un-promoted) deployment at the head of
    the chain. Useful for displaying 'this deployment originated
    from build #X' in the UI."""
    chain = walk_lineage(leaf=leaf, by_id=by_id)
    return chain[-1]
