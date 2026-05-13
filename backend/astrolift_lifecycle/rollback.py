"""
Rollback target resolution + plan (#68, spec 14 §15).

Pure-Python policy. The ``RollbackDeploymentWorkflow`` consults
this for:

* **Target resolution** — given an app, pick the right historical
  deployment to roll back to. Default: the last ``running``
  deployment that isn't the current one. Explicit target via
  ``--to <deployment_id>`` overrides.
* **Plan emission** — produce a ``RollbackPlan`` carrying the
  source deployment's digest + config snapshot. The actual deploy
  reuses ``DeployAppWorkflow`` with these values, so rollbacks
  share the same delivery-mode dispatch as forward deploys.

Pairs with the promotion module (#63) — both reuse
``DeploymentRef``-shape data. Lineage is recorded as
``rolled_back_from_id`` on the new deployment (different field
than ``promoted_from_id`` so the UI distinguishes 'this was a
rollback' from 'this was a promotion').
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import StrEnum


class DeploymentState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    SUPERSEDED = "superseded"


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    """Minimum projection of a Deployment row the resolver needs."""

    deployment_id: int
    app_id: int
    environment_id: int
    state: DeploymentState
    image_digest: str
    config_snapshot: str
    """SHA-256 of the rendered manifest set per #4
    direct-apply.manifest_set_sha256."""

    is_current: bool = False
    """The deployment currently 'live' on the env. Rollback target
    excludes this one by default."""

    created_at_unix: int = 0
    """Used to pick 'most recent' when more than one running
    deployment matches."""


class RollbackError(ValueError):
    pass


def resolve_target(
    *,
    app_id: int,
    environment_id: int,
    candidates: Sequence[DeploymentSnapshot],
    explicit_target_id: int | None = None,
) -> DeploymentSnapshot:
    """Pick the historical deployment to roll back to.

    Rules:
      - Explicit ``--to <id>`` wins; must match one of ``candidates``,
        belong to the same app+env, and have a digest.
      - Otherwise: most recent ``running`` deployment that isn't
        ``is_current``.
      - Raises ``RollbackError`` when no eligible target exists.
    """
    in_scope = [c for c in candidates if c.app_id == app_id and c.environment_id == environment_id]
    if explicit_target_id is not None:
        match = next(
            (c for c in in_scope if c.deployment_id == explicit_target_id),
            None,
        )
        if match is None:
            raise RollbackError(
                f"deployment {explicit_target_id} is not a rollback "
                f"candidate for app {app_id} env {environment_id}"
            )
        if not match.image_digest:
            raise RollbackError(
                f"deployment {explicit_target_id} has no image digest; "
                "cannot guarantee byte-for-byte rollback"
            )
        return match

    eligible = [
        c for c in in_scope if c.state == DeploymentState.RUNNING and not c.is_current and c.image_digest
    ]
    if not eligible:
        raise RollbackError(
            f"no rollback target for app {app_id} env {environment_id}: "
            "need at least one prior running deployment with a digest"
        )
    eligible.sort(key=lambda c: c.created_at_unix, reverse=True)
    return eligible[0]


@dataclasses.dataclass(frozen=True, slots=True)
class RollbackPlan:
    """The fields the workflow uses to start a fresh deploy with
    the source's exact image."""

    app_id: int
    environment_id: int
    image_digest: str
    config_snapshot: str
    rolled_back_from_id: int


def plan_rollback(
    *,
    current: DeploymentSnapshot,
    target: DeploymentSnapshot,
) -> RollbackPlan:
    """Build the plan. Caller validated target via resolve_target;
    here we just enforce the same-app/env invariant once more
    defensively (cheap; protects against shape mismatches in
    tests + edge-case GraphQL inputs).
    """
    if current.app_id != target.app_id:
        raise RollbackError(f"current app {current.app_id} != target app {target.app_id}")
    if current.environment_id != target.environment_id:
        raise RollbackError(f"current env {current.environment_id} != target env {target.environment_id}")
    if not target.image_digest:
        raise RollbackError("target has no image digest; cannot guarantee byte-for-byte rollback")
    return RollbackPlan(
        app_id=target.app_id,
        environment_id=target.environment_id,
        image_digest=target.image_digest,
        config_snapshot=target.config_snapshot,
        rolled_back_from_id=current.deployment_id,
    )
