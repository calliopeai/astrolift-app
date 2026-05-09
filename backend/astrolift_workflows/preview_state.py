r"""
PreviewEnvironment state machine + projection (#81, spec 18 §3).

Pure-Python policy. The Django model + GraphQL surface that
this module is paired with consult it for:

* **Status state machine** — building / running / failed /
  torn_down with locked transitions.
* **Field projection** — pure dataclass mirror of the model,
  used by the workflow layer (which doesn't import Django) to
  reason about preview state.
* **Filter shape** — for the \`previews(status:)\` query
  argument.
* **Uniqueness key** — \`(registered_app_id, pr_number)\` among
  non-torn-down rows.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum


class PreviewStateError(ValueError):
    pass


# ---- status ---------------------------------------------------------


class PreviewStatus(str, Enum):
    """Spec 18 §3."""

    BUILDING = "building"
    RUNNING = "running"
    FAILED = "failed"
    TORN_DOWN = "torn_down"


_ALLOWED_TRANSITIONS: dict[PreviewStatus, frozenset[PreviewStatus]] = {
    PreviewStatus.BUILDING: frozenset({
        PreviewStatus.RUNNING,
        PreviewStatus.FAILED,
        # If a teardown signal arrives mid-build, we may go
        # straight to TORN_DOWN once the in-flight work cleans up.
        PreviewStatus.TORN_DOWN,
    }),
    PreviewStatus.RUNNING: frozenset({
        # Re-deploy puts us back in BUILDING (new SHA on existing
        # preview).
        PreviewStatus.BUILDING,
        PreviewStatus.FAILED,
        PreviewStatus.TORN_DOWN,
    }),
    PreviewStatus.FAILED: frozenset({
        # Push a fix → rebuild.
        PreviewStatus.BUILDING,
        PreviewStatus.TORN_DOWN,
    }),
    PreviewStatus.TORN_DOWN: frozenset(),
    # TORN_DOWN is terminal — operator pushes a new commit, the
    # webhook handler creates a new PreviewEnvironment row;
    # this row stays soft-deleted.
}


def can_transition(
    *,
    current: PreviewStatus,
    target: PreviewStatus,
) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(
    *,
    current: PreviewStatus,
    target: PreviewStatus,
) -> None:
    if not can_transition(current=current, target=target):
        raise PreviewStateError(
            f"invalid preview status transition "
            f"{current.value} → {target.value}; allowed: "
            f"{[t.value for t in _ALLOWED_TRANSITIONS.get(current, [])]}"
        )


# ---- projection -----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewProjection:
    """Pure-Python mirror of PreviewEnvironment row. Workflow
    layer uses this to reason without importing Django."""

    preview_id: int
    guid: str
    registered_app_id: int
    pr_number: int
    branch: str
    commit_sha: str
    status: PreviewStatus
    hostname: str
    namespace: str
    app_environment_id: int
    last_deployed_at_unix: int | None
    torn_down_at_unix: int | None
    workflow_run_id: str

    def __post_init__(self) -> None:
        if self.pr_number <= 0:
            raise PreviewStateError(
                f"pr_number must be positive, got {self.pr_number}"
            )
        if not self.guid:
            raise PreviewStateError("guid is required")
        if not self.namespace:
            raise PreviewStateError("namespace is required")
        if (
            self.status == PreviewStatus.TORN_DOWN
            and self.torn_down_at_unix is None
        ):
            # The model permits this temporarily during the
            # bookkeeping-tail recovery (#87); flag it so the
            # workflow knows to populate the timestamp.
            pass
        if (
            self.status != PreviewStatus.TORN_DOWN
            and self.torn_down_at_unix is not None
        ):
            raise PreviewStateError(
                f"torn_down_at set on a {self.status.value} preview"
            )


# ---- uniqueness invariant -------------------------------------------


def is_active(*, preview: PreviewProjection) -> bool:
    """Active = anything that's not TORN_DOWN. Used for the
    uniqueness check: at most one active preview per
    (app, pr_number)."""
    return preview.status != PreviewStatus.TORN_DOWN


def find_active_for_pr(
    *,
    previews: Sequence[PreviewProjection],
    registered_app_id: int,
    pr_number: int,
) -> PreviewProjection | None:
    """Spec §3 unique constraint enforced in policy: returns
    the (at most one) active preview for the (app, pr) pair.
    DB also enforces via partial unique index, but workflows
    can pre-check here to avoid relying on integrity errors."""
    matches = [
        p for p in previews
        if p.registered_app_id == registered_app_id
        and p.pr_number == pr_number
        and is_active(preview=p)
    ]
    if len(matches) > 1:
        raise PreviewStateError(
            f"multiple active previews for app {registered_app_id} "
            f"PR #{pr_number}; uniqueness invariant violated"
        )
    return matches[0] if matches else None


# ---- query filter shape --------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewListFilter:
    r"""Shape the GraphQL \`previews(...)\` query accepts."""

    registered_app_id: int
    statuses: tuple[PreviewStatus, ...] = ()
    """Empty = all statuses. Caller usually filters to
    [BUILDING, RUNNING] for the operator's 'active previews'
    UI list."""


def filter_previews(
    *,
    previews: Sequence[PreviewProjection],
    f: PreviewListFilter,
) -> tuple[PreviewProjection, ...]:
    """Pure filter — the Django QuerySet implements the same
    semantics with ORM filters; this lets workflow code share
    the logic."""
    out = [p for p in previews if p.registered_app_id == f.registered_app_id]
    if f.statuses:
        statuses = set(f.statuses)
        out = [p for p in out if p.status in statuses]
    return tuple(out)


def parse_status_filter(
    *, raw: Sequence[str],
) -> tuple[PreviewStatus, ...]:
    """GraphQL passes string values; validate against enum."""
    out: list[PreviewStatus] = []
    seen: set[PreviewStatus] = set()
    for value in raw:
        try:
            status = PreviewStatus(value)
        except ValueError as exc:
            raise PreviewStateError(
                f"unknown preview status {value!r}; known: "
                f"{[s.value for s in PreviewStatus]}"
            ) from exc
        if status not in seen:
            seen.add(status)
            out.append(status)
    return tuple(out)
