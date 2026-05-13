"""
Managed service lifecycle policy (#20, spec 11 §5 + §12).

Pure-Python policy. The Temporal workflows
(``ProvisionManagedServiceWorkflow``, etc.) call into this for:

* **Idempotency keys** for provision/update so re-running a
  workflow on an existing resource reconciles rather than
  recreates.
* **Delta detection** for update — diff incoming spec vs current
  handle to decide whether the driver call is needed at all.
* **Snapshot retention policy** — scheduled-snapshot rules
  (nightly, retain N) that the snapshot workflow consults.
* **Soft-delete vs hard-delete** semantics for deprovision.

Pairs with the existing modules:
  * #11 ProvisionSpec / ManagedServiceHandle / Binding
  * #150 RotationPolicy for the credential rotation cadence
  * #25 connection_secret for re-rendering after rotation
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum

# ---- idempotency ---------------------------------------------------


def idempotency_key(
    *,
    org_slug: str,
    app_slug: str,
    env_slug: str,
    binding_name: str,
    kind: str,
) -> str:
    """Stable key per (org, app, env, binding, kind). Workflow
    sets this as the Temporal workflow id so re-firing the same
    deploy joins the existing run rather than creating a parallel
    one. Per spec 11 §5: provision is idempotent."""
    parts = "|".join((org_slug, app_slug, env_slug, binding_name, kind))
    digest = hashlib.sha256(parts.encode("utf-8")).hexdigest()[:16]
    return f"msvc-{org_slug}-{app_slug}-{env_slug}-{binding_name}-{digest}"


# ---- delta detection ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SpecDelta:
    """Per-field diff between two ProvisionSpecs. Emptiness means
    no driver call needed."""

    changed_fields: tuple[str, ...]
    requires_driver_call: bool

    @property
    def is_empty(self) -> bool:
        return not self.changed_fields


def diff_spec(
    *,
    current: Mapping[str, object],
    desired: Mapping[str, object],
) -> SpecDelta:
    """Compute changed fields between two spec snapshots. The
    workflow uses this to short-circuit unchanged updates.

    Field comparison is exact-match on JSON-serializable values.
    Driver-specific 'soft' fields (e.g. tags that don't require
    re-provision) are filtered out by the caller before passing
    in — this module does pure dict diff.
    """
    changed: list[str] = []
    all_keys = set(current) | set(desired)
    for k in sorted(all_keys):
        if current.get(k) != desired.get(k):
            changed.append(k)
    # Some fields are 'observable only' — tags, descriptions, etc.
    # — and don't require the driver to take action. The caller
    # marks those by passing a non-empty 'metadata-only' set; for
    # this module's contract, every changed field is significant.
    return SpecDelta(
        changed_fields=tuple(changed),
        requires_driver_call=bool(changed),
    )


# ---- deprovision ---------------------------------------------------


class DeprovisionMode(StrEnum):
    SOFT = "soft"
    """Mark the binding deleted; KEEP the underlying resource +
    snapshots. Recovery flow re-binds within retention window."""

    HARD = "hard"
    """Drop the underlying resource AND its snapshots. Operator
    must explicitly opt in (manifest ``delete_data = true`` plus
    a confirmation token); never the default."""


@dataclasses.dataclass(frozen=True, slots=True)
class DeprovisionDecision:
    mode: DeprovisionMode
    delete_data: bool
    delete_snapshots: bool


def plan_deprovision(
    *,
    delete_data: bool,
    operator_confirmed: bool = False,
) -> DeprovisionDecision:
    """Translate the manifest's ``delete_data`` flag into the
    actual driver action.

    Soft delete (default) keeps everything; the binding row is
    marked deleted but the cloud-side resource + snapshots
    remain. Hard delete requires ``delete_data=True`` AND
    ``operator_confirmed=True`` — both. Single flag is too easy
    to leave on by mistake; double-opt-in catches misclicks.
    """
    if delete_data and operator_confirmed:
        return DeprovisionDecision(
            mode=DeprovisionMode.HARD,
            delete_data=True,
            delete_snapshots=True,
        )
    if delete_data and not operator_confirmed:
        # Half-asked-for hard delete: operator hasn't confirmed.
        # Fall back to soft delete and surface a UI prompt.
        return DeprovisionDecision(
            mode=DeprovisionMode.SOFT,
            delete_data=False,
            delete_snapshots=False,
        )
    return DeprovisionDecision(
        mode=DeprovisionMode.SOFT,
        delete_data=False,
        delete_snapshots=False,
    )


# ---- snapshot retention --------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SnapshotPolicy:
    """Per-binding scheduled snapshot rule."""

    schedule: str
    """Cron expression. ``'0 2 * * *'`` = nightly at 02:00."""

    retain_count: int
    """How many snapshots to keep. Older ones are pruned by the
    retention workflow on each new snapshot."""

    def __post_init__(self) -> None:
        if self.retain_count <= 0:
            raise ValueError("retain_count must be positive")
        if not self.schedule:
            raise ValueError("schedule (cron expression) is required")


@dataclasses.dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """Minimal projection of a snapshot row the retention logic
    consumes."""

    snapshot_id: str
    created_at_unix: int


def snapshots_to_prune(
    *,
    snapshots: Sequence[SnapshotRecord],
    policy: SnapshotPolicy,
) -> tuple[SnapshotRecord, ...]:
    """Return the snapshots that exceed the retain_count, oldest
    first (workflow deletes them in that order). Most-recent
    ``retain_count`` snapshots are preserved."""
    if not snapshots:
        return ()
    sorted_snaps = sorted(
        snapshots,
        key=lambda s: s.created_at_unix,
        reverse=True,
    )
    if len(sorted_snaps) <= policy.retain_count:
        return ()
    return tuple(sorted_snaps[policy.retain_count :])


# ---- credential rotation -------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RotationOutcome:
    """What a successful rotation produced. The workflow uses
    these to re-render the in-cluster secret + trigger a pod
    rollout."""

    new_credential_value: str
    rotation_completed_at: datetime
    must_redeploy_workloads: bool
    """True for kinds where the new credential isn't accepted by
    the running pod's existing connection (most DBs accept the
    new password on next connect, but some auth modes require
    pod restart)."""


def rotation_requires_redeploy(*, kind: str, auth_mode: str) -> bool:
    """Decide whether the new credential needs a pod restart to
    take effect.

    Password-mode rotations DON'T require restart — the workload
    picks up the new password on next connection. mTLS rotations
    DO — certs are read at TLS-handshake-init and cached for the
    connection lifetime, so existing connections keep the old
    cert until they're closed.

    IAM mode never requires restart (creds are re-fetched per
    request via workload identity).

    ``kind`` is currently unused but accepted so callers can pass
    it without conditional kwarg passing; future kind-specific
    overrides slot in here.
    """
    del kind  # currently unused; reserved for future per-kind overrides
    return auth_mode == "mtls"
