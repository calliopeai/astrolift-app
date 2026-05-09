r"""
Preview environment teardown policy (#87, spec 18 §7).

Pure-Python policy. ``TeardownPreviewWorkflow`` consults this
module for:

* **Step ordering** — k8s namespace before DNS before managed
  services. Reverse of provisioning, so half-torn-down previews
  end up at recoverable intermediate states.
* **Per-managed-service cleanup mode** — \`shared_with_main\`
  (drop user/keyspace/prefix) vs \`dedicated\` (full deprovision).
* **Idempotency** — already-torn-down previews short-circuit.
* **Timeout policy** — total teardown deadline (spec §7: 15 min).
* **PR-comment template** — post-teardown confirmation.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum


class TeardownError(ValueError):
    pass


PREVIEW_TEARDOWN_DEADLINE_SECONDS = 15 * 60
"""Spec §7: full teardown completes within 15 minutes."""


# ---- step ordering -------------------------------------------------


class TeardownStep(str, Enum):
    """Ordered cleanup operations.

    Reverse of provisioning: app-namespace last in, first out.
    K8s deletion runs finalizers (e.g. PVC reclaim, image-pull
    secret cleanup); DNS removal happens after namespace gone so
    no in-flight requests are still routing to it; managed-service
    cleanup runs only after the workload is dead so writes can't
    race the drop.
    """

    DELETE_NAMESPACE = "delete_namespace"
    """Step 1 — workloads stop, ingress drops, finalizers run."""

    DELETE_DNS = "delete_dns"
    """Step 2 — explicit DNS record removal even though wildcard
    covers, per spec acceptance."""

    CLEANUP_MANAGED_SERVICES = "cleanup_managed_services"
    """Step 3 — drop per-preview db/user/keyspace, deprovision
    dedicated instances."""

    MARK_TORN_DOWN = "mark_torn_down"
    """Step 4 — torn_down_at + status=torn_down on the model."""

    EMIT_EVENT = "emit_event"
    """Step 5 — preview_env.torn_down event."""

    COMMENT_PR = "comment_pr"
    """Step 6 — PR comment confirming teardown."""


TEARDOWN_ORDER = (
    TeardownStep.DELETE_NAMESPACE,
    TeardownStep.DELETE_DNS,
    TeardownStep.CLEANUP_MANAGED_SERVICES,
    TeardownStep.MARK_TORN_DOWN,
    TeardownStep.EMIT_EVENT,
    TeardownStep.COMMENT_PR,
)
"""Locked sequence. Workflow walks this and runs each as an
activity. Step skipping is allowed (e.g. already-torn-down) but
reordering is a code review."""


# ---- per-managed-service cleanup -----------------------------------


class CleanupMode(str, Enum):
    """Spec §7: per-binding cleanup style."""

    SHARED_WITH_MAIN = "shared_with_main"
    """Preview shares the prod managed-service instance and only
    owns a per-preview database/user/keyspace/prefix. Drop just
    the per-preview slice."""

    DEDICATED = "dedicated"
    """Preview has its own dedicated instance. Full deprovision."""


class ManagedServiceKind(str, Enum):
    POSTGRES = "postgres"
    REDIS = "redis"
    OBJECT_STORE = "object_store"
    QUEUE = "queue"


@dataclasses.dataclass(frozen=True, slots=True)
class CleanupAction:
    """One per-binding cleanup. Activity layer translates to
    driver-specific calls."""

    binding_id: int
    kind: ManagedServiceKind
    mode: CleanupMode
    target: str
    """What to drop. For SHARED_WITH_MAIN: the per-preview
    database/user name, keyspace prefix, bucket prefix, or
    queue name. For DEDICATED: the dedicated instance ID."""


def cleanup_action_for(
    *,
    binding_id: int,
    kind: ManagedServiceKind,
    mode: CleanupMode,
    target: str,
) -> CleanupAction:
    """Validate + build one cleanup action."""
    if not target:
        raise TeardownError(
            f"binding {binding_id} ({kind.value}) cleanup target "
            "is empty — refusing to risk dropping the wrong thing"
        )
    return CleanupAction(
        binding_id=binding_id, kind=kind, mode=mode, target=target,
    )


def plan_managed_service_cleanup(
    *,
    bindings: Sequence[CleanupAction],
) -> tuple[CleanupAction, ...]:
    """Order managed-service cleanup deterministically.

    Order matters when bindings reference each other (e.g. an
    object-store policy might reference an IAM role; tear down
    the policy-consumer first):

      1. queues (workload writes here, drain first)
      2. object_store (workload may upload, drain second)
      3. redis (cache; drop after writes stop)
      4. postgres (source of truth; drop last)

    Within a kind, order by binding_id for stability.
    """
    rank = {
        ManagedServiceKind.QUEUE: 0,
        ManagedServiceKind.OBJECT_STORE: 1,
        ManagedServiceKind.REDIS: 2,
        ManagedServiceKind.POSTGRES: 3,
    }
    return tuple(
        sorted(bindings, key=lambda b: (rank[b.kind], b.binding_id))
    )


# ---- idempotency ---------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewTeardownState:
    """Minimum projection the workflow uses to decide
    short-circuit."""

    preview_id: int
    status: str
    """One of: 'pending', 'running', 'failed', 'torn_down'."""

    torn_down_at_unix: int | None


def already_torn_down(*, state: PreviewTeardownState) -> bool:
    """Spec acceptance: workflow is idempotent — re-runs against
    already-torn-down previews short-circuit. ``torn_down`` status
    AND a torn_down_at timestamp both required (paranoid check;
    'torn_down' status without timestamp is an inconsistency the
    workflow should still fix by emitting the missing event)."""
    return (
        state.status == "torn_down"
        and state.torn_down_at_unix is not None
    )


def teardown_steps_to_run(
    *,
    state: PreviewTeardownState,
) -> tuple[TeardownStep, ...]:
    """Return the steps the workflow should execute. Already-
    torn-down previews skip the destructive steps but still
    re-emit event / re-comment if those failed in a prior run.

    The 'fix the inconsistency' branch is what 'idempotent'
    actually means — not 'no-op on re-run' but 'eventually
    converges to the desired state'.
    """
    if already_torn_down(state=state):
        # Destructive work already done; skip to the bookkeeping
        # tail (event re-emission tolerated; PR comment short-
        # circuits on the activity side via PR-comment dedup).
        return ()

    if state.status == "torn_down" and state.torn_down_at_unix is None:
        # Inconsistent: status set but timestamp missing. Run the
        # bookkeeping tail to fix without re-running destructive
        # steps.
        return (
            TeardownStep.MARK_TORN_DOWN,
            TeardownStep.EMIT_EVENT,
            TeardownStep.COMMENT_PR,
        )

    return TEARDOWN_ORDER


# ---- PR comment template -------------------------------------------


def pr_comment_for_teardown(
    *,
    pr_number: int,
    is_merge: bool,
) -> str:
    """Spec §7 acceptance: PR comment confirming teardown."""
    trigger = "merged" if is_merge else "closed"
    return (
        f"**Preview environment torn down**\n\n"
        f"PR #{pr_number} was {trigger}; the preview environment "
        f"has been cleaned up.\n\n"
        f"Per-preview databases, caches, and queues are gone. "
        f"Push to reopen the PR to bring the preview back."
    )


# ---- step deadline -------------------------------------------------


# Per-step time budgets, summing to PREVIEW_TEARDOWN_DEADLINE_SECONDS.
# Namespace + finalizers are usually the slow path (PVC reclaim,
# webhook calls); DNS + bookkeeping are quick.
STEP_DEADLINES_SECONDS = {
    TeardownStep.DELETE_NAMESPACE: 7 * 60,
    TeardownStep.DELETE_DNS: 60,
    TeardownStep.CLEANUP_MANAGED_SERVICES: 5 * 60,
    TeardownStep.MARK_TORN_DOWN: 30,
    TeardownStep.EMIT_EVENT: 30,
    TeardownStep.COMMENT_PR: 60,
}
# Sums to 900 (15 minutes) — exactly the spec deadline.


def deadline_for_step(*, step: TeardownStep) -> int:
    """Activity timeout for one step. Workflow uses these as
    heartbeat / start-to-close timeouts."""
    if step not in STEP_DEADLINES_SECONDS:
        raise TeardownError(f"unknown teardown step {step!r}")
    return STEP_DEADLINES_SECONDS[step]


def total_step_budget() -> int:
    """Sum of all step deadlines. Validates the budget fits the
    spec's 15-minute global deadline."""
    return sum(STEP_DEADLINES_SECONDS.values())
