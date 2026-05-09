"""
Data residency / region pinning policy (#152, spec 12 §14).

Org admins can restrict which regions and clusters an org's apps
deploy to. The deploy workflow consults this module before each
target selection; cross-region service bindings get the same check.

Why a separate module:

- The check is hot-path (every deploy + every binding) and pure —
  no DB writes, no driver calls. Keeping it in a small module
  makes it cheap to import from workflows + activities + the
  GraphQL layer ("this org can't pick that region" UI).
- Region metadata flows in as plain strings so the module doesn't
  reach into ``astrolift_clusters.TenantCluster`` — callers project
  the cluster row down to ``(cluster_id, region)`` first. Tests
  don't need a full Django setup.

Defaults: an org with no constraints (``allowed_regions=()``) can
deploy anywhere. Constraints are an explicit allow-list, never a
block-list — fail-closed once the admin opts in.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Final

# Sentinel for "no constraint" — explicit so callers don't confuse
# an empty list ('nothing allowed') with absence of a policy
# ('no constraint at all'). The UI surfaces both states differently.
NO_CONSTRAINT: Final[tuple[str, ...]] = ()


class ResidencyViolation(ValueError):
    """A deploy or binding targeted a region the org is pinned out of."""

    def __init__(
        self,
        *,
        org_slug: str,
        target_region: str,
        allowed_regions: Sequence[str],
        reason: str = "",
    ):
        self.org_slug = org_slug
        self.target_region = target_region
        self.allowed_regions = tuple(allowed_regions)
        self.reason = reason
        msg = (
            f"data residency violation: org {org_slug!r} cannot deploy to "
            f"region {target_region!r}; allowed regions: "
            f"{sorted(allowed_regions) or '<none>'}"
        )
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


@dataclasses.dataclass(frozen=True, slots=True)
class ResidencyPolicy:
    """The per-org pin set.

    ``allowed_regions=()`` is the unconfigured state ("no constraint",
    deploy anywhere). Once the admin opts in, the list is the
    canonical allow-list — fail-closed.

    ``allowed_cluster_ids`` is a tighter override: when non-empty,
    only those specific clusters are allowed (region check still
    applies, but the cluster pin lets an admin lock down to a
    specific named cluster within an allowed region).
    """

    org_slug: str
    allowed_regions: tuple[str, ...] = NO_CONSTRAINT
    allowed_cluster_ids: tuple[int, ...] = ()


def is_constrained(policy: ResidencyPolicy) -> bool:
    """True iff the org has opted in to residency pinning at all."""
    return bool(policy.allowed_regions) or bool(policy.allowed_cluster_ids)


def check_target(
    policy: ResidencyPolicy,
    *,
    cluster_id: int,
    cluster_region: str,
) -> None:
    """Raise :class:`ResidencyViolation` if ``cluster_id``/``region``
    isn't allowed under ``policy``. Returns None on success.

    Both checks are independent: a cluster ID in the allow-list still
    has its region checked. The reverse is not strictly necessary but
    it catches a misconfiguration where a cluster's region was
    relabeled out from under a stale pin.
    """
    if not is_constrained(policy):
        return  # no policy → anywhere

    if policy.allowed_regions and cluster_region not in policy.allowed_regions:
        raise ResidencyViolation(
            org_slug=policy.org_slug,
            target_region=cluster_region,
            allowed_regions=policy.allowed_regions,
        )
    if policy.allowed_cluster_ids and cluster_id not in policy.allowed_cluster_ids:
        raise ResidencyViolation(
            org_slug=policy.org_slug,
            target_region=cluster_region,
            allowed_regions=policy.allowed_regions,
            reason=(
                f"cluster {cluster_id} is not in the allowed cluster set"
                f" {sorted(policy.allowed_cluster_ids)}"
            ),
        )


def check_binding(
    policy: ResidencyPolicy,
    *,
    workload_region: str,
    bound_resource_region: str,
    bound_resource_kind: str,
) -> None:
    """Raise on a service binding that crosses region boundaries.

    Some service kinds are inherently region-pinned (postgres,
    redis, object_store). Binding a workload in ``us-east-1`` to a
    postgres in ``eu-west-1`` is a residency violation regardless of
    whether both regions are individually allowed: data leaves the
    region on every query.

    Workloads bound to globally-replicated services (``cdn``, some
    secret backends) sidestep this check; callers pass the kind so
    we can keep the rule one-line.
    """
    GLOBAL_KINDS = {"cdn", "kms_global"}
    if bound_resource_kind in GLOBAL_KINDS:
        return
    if not is_constrained(policy):
        # Even unconstrained orgs get the cross-region check —
        # silent cross-region bindings are an outage waiting to
        # happen (latency + egress + GDPR all at once).
        if workload_region != bound_resource_region:
            raise ResidencyViolation(
                org_slug=policy.org_slug,
                target_region=bound_resource_region,
                allowed_regions=(workload_region,),
                reason=(
                    f"cross-region binding ({bound_resource_kind}: workload "
                    f"in {workload_region!r}, resource in "
                    f"{bound_resource_region!r})"
                ),
            )
        return

    if workload_region != bound_resource_region:
        raise ResidencyViolation(
            org_slug=policy.org_slug,
            target_region=bound_resource_region,
            allowed_regions=policy.allowed_regions,
            reason=(
                f"cross-region binding ({bound_resource_kind}: workload "
                f"in {workload_region!r}, resource in "
                f"{bound_resource_region!r})"
            ),
        )
