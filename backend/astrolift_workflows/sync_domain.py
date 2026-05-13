r"""
SyncAppDomainWorkflow policy (#143, spec 06 §4.17).

Pure-Python policy. Reconciles DNS + ingress + cert state when
an app's subdomain or custom domain changes, WITHOUT triggering
a full redeploy.

* **Step ordering** — DNS → ingress → cert in that order. Each
  step is reversible and rollback restores the previous state.
* **Diff classification** — given (old, new) hostname pairs,
  emit per-step actions: create / update / delete.
* **Cert reuse** — when the new hostname is covered by an
  existing wildcard cert (e.g. \`*.org.<zone>\`), skip the
  per-domain cert request.
* **Rollback plan** — if any step fails, undo the prior steps
  in reverse order to restore the original routing.

Pairs with #57 (custom domain DNS state machine) and #70 (TLS
issuance). RotateDeployTokenWorkflow already shipped in #130
(utility_workflows.py); this module covers SyncAppDomainWorkflow
which #143 tracks alongside it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import StrEnum


class SyncDomainError(ValueError):
    pass


# ---- step ordering -------------------------------------------------


class SyncStep(StrEnum):
    """Spec 06 §4.17."""

    UPDATE_DNS = "update_dns"
    """Create/update/delete A/CNAME records for the new domain."""

    PATCH_INGRESS = "patch_ingress"
    """Add/remove host rules on the app's Ingress / Gateway /
    VirtualService."""

    REQUEST_CERT = "request_cert"
    """Trigger cert-manager Certificate for the new domain.
    Skipped when the new hostname is covered by an existing
    wildcard cert."""

    WAIT_PROPAGATION = "wait_propagation"
    """DNS propagation + cert issuance latency. Activity layer
    polls; this module just declares the step."""

    VERIFY_E2E = "verify_e2e"
    """Resolve the new domain through a public resolver and
    confirm TLS terminates with the right cert."""


SYNC_ORDER = (
    SyncStep.UPDATE_DNS,
    SyncStep.PATCH_INGRESS,
    SyncStep.REQUEST_CERT,
    SyncStep.WAIT_PROPAGATION,
    SyncStep.VERIFY_E2E,
)


# ---- diff classification -------------------------------------------


class HostnameAction(StrEnum):
    """What to do with a hostname during sync."""

    ADD = "add"
    """Hostname is in the new set but not the old."""

    REMOVE = "remove"
    """Hostname is in the old set but not the new."""

    KEEP = "keep"
    """Same in both sets — no DNS/ingress action needed."""


@dataclasses.dataclass(frozen=True, slots=True)
class HostnameDelta:
    hostname: str
    action: HostnameAction


def diff_hostnames(
    *,
    old: Sequence[str],
    new: Sequence[str],
) -> tuple[HostnameDelta, ...]:
    """Compute the diff between two hostname sets.

    Returns deltas in deterministic order: removes first, then
    adds, then keeps. Removes-first-adds-second matters when
    hostname routes share an ingress controller's max-routes
    quota; doing the removes first frees capacity for the adds.
    """
    old_set = {h.lower() for h in old}
    new_set = {h.lower() for h in new}

    removes = sorted(old_set - new_set)
    adds = sorted(new_set - old_set)
    keeps = sorted(old_set & new_set)

    out: list[HostnameDelta] = []
    out.extend(HostnameDelta(hostname=h, action=HostnameAction.REMOVE) for h in removes)
    out.extend(HostnameDelta(hostname=h, action=HostnameAction.ADD) for h in adds)
    out.extend(HostnameDelta(hostname=h, action=HostnameAction.KEEP) for h in keeps)
    return tuple(out)


# ---- cert reuse ----------------------------------------------------


def is_covered_by_wildcard(
    *,
    hostname: str,
    wildcard_sans: Sequence[str],
) -> bool:
    """Spec §4.17: skip per-domain cert when an existing wildcard
    covers the new hostname. Wildcard SANs match exactly one
    label deeper (RFC 6125): ``*.example.com`` matches
    ``foo.example.com`` but NOT ``foo.bar.example.com``."""
    hostname = hostname.lower().rstrip(".")
    if not hostname:
        return False

    for san in wildcard_sans:
        san = san.lower().rstrip(".")
        if not san.startswith("*."):
            # Non-wildcard SAN: exact match only
            if san == hostname:
                return True
            continue
        # Wildcard: matches one-label-deep ONLY
        suffix = san[2:]  # drop '*.'
        if not hostname.endswith("." + suffix):
            continue
        # Verify the prefix is exactly one label
        prefix = hostname[: -(len(suffix) + 1)]
        if prefix and "." not in prefix:
            return True
    return False


def needs_per_domain_cert(
    *,
    hostname: str,
    wildcard_sans: Sequence[str] = (),
) -> bool:
    """Inverse of is_covered_by_wildcard with helpful default
    for callers that don't pass any wildcards (e.g. custom
    domains where there's never a wildcard)."""
    if not hostname:
        raise SyncDomainError("hostname is required")
    return not is_covered_by_wildcard(
        hostname=hostname,
        wildcard_sans=wildcard_sans,
    )


# ---- rollback plan -------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SyncProgress:
    """How far the sync got before failing. The workflow updates
    this after each successful step; rollback consults to know
    what to undo."""

    completed_steps: tuple[SyncStep, ...]
    failed_step: SyncStep | None


def rollback_steps(*, progress: SyncProgress) -> tuple[SyncStep, ...]:
    """Reverse-order undo of completed steps.

    WAIT_PROPAGATION + VERIFY_E2E are pure observation — no undo
    needed. UPDATE_DNS, PATCH_INGRESS, REQUEST_CERT are state
    changes that need reversing.
    """
    reversible = {
        SyncStep.UPDATE_DNS,
        SyncStep.PATCH_INGRESS,
        SyncStep.REQUEST_CERT,
    }
    to_reverse = tuple(s for s in progress.completed_steps if s in reversible)
    return tuple(reversed(to_reverse))


def is_rollback_needed(*, progress: SyncProgress) -> bool:
    """Skip rollback when nothing was done (failed at the very
    first step) or when the workflow finished cleanly."""
    if progress.failed_step is None:
        return False
    return any(
        s
        in {
            SyncStep.UPDATE_DNS,
            SyncStep.PATCH_INGRESS,
            SyncStep.REQUEST_CERT,
        }
        for s in progress.completed_steps
    )


# ---- propagation timeout -------------------------------------------


DNS_PROPAGATION_MAX_SECONDS = 5 * 60
"""Public DNS propagation usually < 5 min; longer = problem with
delegation or zone config. Operator should investigate rather
than waiting indefinitely."""

CERT_ISSUANCE_MAX_SECONDS = 10 * 60
"""ACME issuance + DNS-01 challenge round-trip; > 10 min usually
means the challenge response isn't reachable."""


def step_deadline_seconds(*, step: SyncStep) -> int:
    """Activity timeout per step."""
    if step == SyncStep.UPDATE_DNS:
        return 60
    if step == SyncStep.PATCH_INGRESS:
        return 60
    if step == SyncStep.REQUEST_CERT:
        return CERT_ISSUANCE_MAX_SECONDS
    if step == SyncStep.WAIT_PROPAGATION:
        return DNS_PROPAGATION_MAX_SECONDS
    if step == SyncStep.VERIFY_E2E:
        return 60
    raise SyncDomainError(f"unknown sync step {step!r}")
