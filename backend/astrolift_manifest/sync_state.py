"""
Manifest sync state machine (#132, spec 05 + spec 07).

Pure-Python policy. Tracks divergence between the platform DB's
manifest snapshot and the repo manifest, classifying each
RegisteredApp into one of four states:

  in_sync     — DB config hash == last-observed repo hash
  db_ahead    — user edited config in UI/API; repo is stale
  repo_ahead  — developer pushed new manifest; DB hasn't synced
  diverged    — both sides changed independently

Pairs with #4 manifest_set_sha256 (we reuse the same canonical
JSON hash). The webhook handler (#93) and the UI mutation path
both call into ``classify_state`` to keep the badge accurate.
"""

from __future__ import annotations

import dataclasses
from enum import Enum


class SyncState(str, Enum):
    IN_SYNC = "in_sync"
    DB_AHEAD = "db_ahead"
    REPO_AHEAD = "repo_ahead"
    DIVERGED = "diverged"


@dataclasses.dataclass(frozen=True, slots=True)
class SyncSnapshot:
    """The three hashes the policy needs.

    ``last_synced_hash`` is the hash that was true on BOTH sides
    at the last reconciliation point. Without it we can't
    distinguish 'db_ahead' from 'diverged' — knowing both sides
    differ from each other isn't enough; we need the historical
    anchor.
    """

    db_hash: str
    """Current hash of the DB-side config snapshot."""

    repo_hash: str
    """Current hash of the manifest in the repo (most recent
    push or polled commit)."""

    last_synced_hash: str
    """Hash that was on both sides at the last sync point. May
    be empty before the app's first sync."""


def classify_state(snap: SyncSnapshot) -> SyncState:
    """Pure classifier.

    Truth table (db, repo, last):
      (X, X, *)        -> in_sync
      (X, X, X)        -> in_sync (regardless of last; both equal)
      (X, Y, X)        -> db_ahead (repo == last; db moved)
      (X, Y, Y)        -> repo_ahead (db == last; repo moved)
      (X, Y, Z)        -> diverged (neither side == last)
      (X, Y, '')       -> diverged (no anchor; conservative)
    """
    if snap.db_hash == snap.repo_hash:
        return SyncState.IN_SYNC

    if snap.last_synced_hash:
        if snap.db_hash == snap.last_synced_hash:
            # Repo moved off the anchor; DB stayed.
            return SyncState.REPO_AHEAD
        if snap.repo_hash == snap.last_synced_hash:
            # DB moved off the anchor; repo stayed.
            return SyncState.DB_AHEAD

    # Either no anchor (first-ever divergence) or both sides
    # moved off the anchor independently. Either way, can't
    # auto-reconcile.
    return SyncState.DIVERGED


def needs_attention(state: SyncState) -> bool:
    """Convenience for the UI: emit a 'needs sync' badge?"""
    return state != SyncState.IN_SYNC


# ---- transition recording ------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SyncEvent:
    """A reconciliation event the workflow records to advance the
    last_synced_hash anchor."""

    new_anchor_hash: str
    """The hash both sides agreed on at this sync point. The
    workflow writes this to the RegisteredApp row."""

    direction: str  # 'db_to_repo' | 'repo_to_db' | 'manual'
    actor_kind: str  # 'system' | 'user' | 'webhook'


def reconcile_to_repo(
    *,
    snap: SyncSnapshot,
    actor_kind: str = "system",
) -> SyncEvent:
    """Bring DB into alignment with repo (apply repo state to DB).
    Anchor advances to the repo hash."""
    return SyncEvent(
        new_anchor_hash=snap.repo_hash,
        direction="repo_to_db",
        actor_kind=actor_kind,
    )


def reconcile_to_db(
    *,
    snap: SyncSnapshot,
    actor_kind: str = "user",
) -> SyncEvent:
    """Push DB state to the repo (commit DB into manifest).
    Anchor advances to the db hash."""
    return SyncEvent(
        new_anchor_hash=snap.db_hash,
        direction="db_to_repo",
        actor_kind=actor_kind,
    )


# ---- diverged-state guards -----------------------------------------


class SyncReconcileError(ValueError):
    """Raised when the caller asks for an automatic reconcile but
    the state requires human review (DIVERGED)."""


def assert_can_auto_reconcile(state: SyncState) -> None:
    """Auto-reconcile is safe for db_ahead and repo_ahead — only
    one side has changes since last anchor. DIVERGED requires a
    human to pick a winner; the workflow should fail loudly."""
    if state == SyncState.DIVERGED:
        raise SyncReconcileError(
            "cannot auto-reconcile a DIVERGED app — both sides "
            "changed since the last sync point; user must choose "
            "a winner"
        )
