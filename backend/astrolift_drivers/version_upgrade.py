"""
Plugin version upgrade compatibility + phased-rollout policy
(#168, spec 02 §8).

The actual workflow that pins a new version on a TenantCluster
runs in Temporal — this module is the *policy* that workflow
consults at each phase:

* compatibility check: is going from A → B safe given cluster
  capabilities and the plugin's declared dependencies?
* phased-rollout selection: from N candidate clusters, pick the
  next batch (canary first, then graduated rings) so a bad
  version doesn't burn every tenant at once.

Pure module — no Django, no Temporal, no driver imports. Tests
run with plain dicts.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import StrEnum

# ---- semver-ish parsing ----------------------------------------------
#
# We don't pull in a full semver lib — the platform pins plugins to
# strict X.Y.Z and never accepts pre-release / build metadata in
# production pin slots. Anything outside that pattern fails parsing,
# which is the desired behavior (a typo in a manifest shouldn't
# silently match a wildcard).

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclasses.dataclass(frozen=True, slots=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


class VersionParseError(ValueError):
    pass


def parse_version(s: str) -> Version:
    m = _VERSION_RE.match(s.strip())
    if m is None:
        raise VersionParseError(f"version {s!r} not in strict X.Y.Z form (no pre-release / build metadata)")
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ---- compatibility ---------------------------------------------------


class UpgradeKind(StrEnum):
    """Same X.Y.Z numbering as the version itself."""

    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    DOWNGRADE = "downgrade"


def upgrade_kind(*, from_v: Version, to_v: Version) -> UpgradeKind:
    if to_v < from_v:
        return UpgradeKind.DOWNGRADE
    if to_v.major > from_v.major:
        return UpgradeKind.MAJOR
    if to_v.minor > from_v.minor:
        return UpgradeKind.MINOR
    return UpgradeKind.PATCH  # to_v.patch >= from_v.patch (or equal)


@dataclasses.dataclass(frozen=True, slots=True)
class CompatibilityIssue:
    """One blocker discovered by :func:`check_compatibility`."""

    code: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class CompatibilityReport:
    kind: UpgradeKind
    issues: tuple[CompatibilityIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def check_compatibility(
    *,
    from_v: Version,
    to_v: Version,
    cluster_capabilities: set[str],
    required_capabilities: set[str],
    min_platform_version: Version | None,
    current_platform_version: Version,
    allow_downgrade: bool = False,
) -> CompatibilityReport:
    """Decide whether ``to_v`` can replace ``from_v`` on a cluster.

    Checks (in order, all reported together so the operator sees
    every blocker at once):

      1. Downgrade requires explicit ``allow_downgrade=True`` —
         downgrade paths are usually one-way (data migrations rarely
         reverse cleanly).
      2. Major version bumps require explicit operator approval at
         the workflow level — this function reports them as a
         blocker tagged ``major_upgrade_requires_approval`` so the
         workflow gates accordingly.
      3. The cluster must satisfy every capability the new plugin
         requires.
      4. The platform itself must be ≥ ``min_platform_version``
         (catches 'plugin needs astrolift 1.5+ but install is 1.4').
    """
    issues: list[CompatibilityIssue] = []

    kind = upgrade_kind(from_v=from_v, to_v=to_v)

    if kind == UpgradeKind.DOWNGRADE and not allow_downgrade:
        issues.append(
            CompatibilityIssue(
                code="downgrade_blocked",
                message=f"downgrade {from_v} -> {to_v} requires explicit allow_downgrade=True",
            )
        )

    if kind == UpgradeKind.MAJOR:
        issues.append(
            CompatibilityIssue(
                code="major_upgrade_requires_approval",
                message=(f"major version bump {from_v} -> {to_v}; workflow must require operator approval"),
            )
        )

    missing = required_capabilities - cluster_capabilities
    if missing:
        issues.append(
            CompatibilityIssue(
                code="missing_cluster_capabilities",
                message=(f"cluster missing required capabilities: {sorted(missing)}"),
            )
        )

    if min_platform_version is not None and current_platform_version < min_platform_version:
        issues.append(
            CompatibilityIssue(
                code="platform_too_old",
                message=(
                    f"plugin requires platform >= {min_platform_version}; "
                    f"this install is {current_platform_version}"
                ),
            )
        )

    return CompatibilityReport(kind=kind, issues=tuple(issues))


# ---- phased rollout --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RolloutCluster:
    """Minimal projection of a TenantCluster for rollout selection."""

    cluster_id: int
    slug: str
    is_canary: bool = False
    tenant_count: int = 0
    """Used to order a 'small clusters first' progression — limits
    blast radius when something goes wrong on the first non-canary
    ring."""


@dataclasses.dataclass(frozen=True, slots=True)
class RolloutBatch:
    phase: str
    cluster_ids: tuple[int, ...]


# Default rollout shape: canary → 10% → 50% → 100%. Operators can
# override per-plugin if they need stricter gates.
DEFAULT_PHASES: tuple[tuple[str, float], ...] = (
    ("canary", 0.0),  # canary clusters only
    ("ring_1_10pct", 0.10),
    ("ring_2_50pct", 0.50),
    ("ring_3_100pct", 1.0),
)


def plan_rollout(
    *,
    candidates: Sequence[RolloutCluster],
    phases: Sequence[tuple[str, float]] = DEFAULT_PHASES,
) -> list[RolloutBatch]:
    """Plan the phase batches.

    'canary' phase is *every cluster flagged is_canary*; subsequent
    phases progressively widen by tenant count (smallest first) so
    a bad version contains its blast radius.
    """
    canaries = [c for c in candidates if c.is_canary]
    non_canaries = [c for c in candidates if not c.is_canary]
    non_canaries.sort(key=lambda c: (c.tenant_count, c.slug))

    out: list[RolloutBatch] = []
    total = len(non_canaries)
    cumulative_target = 0
    seen_ids: set[int] = set()

    for phase_name, fraction in phases:
        if phase_name == "canary" and fraction == 0.0:
            ids = tuple(c.cluster_id for c in canaries)
            seen_ids.update(ids)
            out.append(RolloutBatch(phase=phase_name, cluster_ids=ids))
            continue

        cumulative_target = max(cumulative_target, int(round(total * fraction)))
        # The set we want INCLUDED by end of phase — slice from
        # already-sorted non-canaries up to ``cumulative_target``.
        # Subtract clusters already shipped to in earlier phases.
        target_set = non_canaries[:cumulative_target]
        new_ids = tuple(c.cluster_id for c in target_set if c.cluster_id not in seen_ids)
        seen_ids.update(new_ids)
        out.append(RolloutBatch(phase=phase_name, cluster_ids=new_ids))

    return out


def remaining_clusters(
    *,
    candidates: Sequence[RolloutCluster],
    completed_batches: Sequence[RolloutBatch],
) -> tuple[int, ...]:
    """Cluster ids not yet shipped (across all completed phases).
    Used by the workflow to pick the next batch to ship in case of
    re-entry after a partial run."""
    shipped: set[int] = set()
    for b in completed_batches:
        shipped.update(b.cluster_ids)
    return tuple(c.cluster_id for c in candidates if c.cluster_id not in shipped)
