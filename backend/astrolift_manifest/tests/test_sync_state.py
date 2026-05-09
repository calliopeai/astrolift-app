"""Tests for manifest sync state machine (#132, spec 05+07)."""

from __future__ import annotations

import pytest

from astrolift_manifest.sync_state import (
    SyncReconcileError,
    SyncSnapshot,
    SyncState,
    assert_can_auto_reconcile,
    classify_state,
    needs_attention,
    reconcile_to_db,
    reconcile_to_repo,
)

# ---- classify_state ------------------------------------------------


def test_in_sync_when_db_equals_repo():
    snap = SyncSnapshot(db_hash="abc", repo_hash="abc", last_synced_hash="abc")
    assert classify_state(snap) == SyncState.IN_SYNC


def test_in_sync_works_without_anchor():
    """Equal hashes mean in-sync regardless of anchor history."""
    snap = SyncSnapshot(db_hash="abc", repo_hash="abc", last_synced_hash="")
    assert classify_state(snap) == SyncState.IN_SYNC


def test_db_ahead_when_repo_equals_anchor():
    """Repo stayed at the anchor; DB moved."""
    snap = SyncSnapshot(
        db_hash="db-new", repo_hash="anchor", last_synced_hash="anchor",
    )
    assert classify_state(snap) == SyncState.DB_AHEAD


def test_repo_ahead_when_db_equals_anchor():
    """DB stayed at the anchor; repo moved."""
    snap = SyncSnapshot(
        db_hash="anchor", repo_hash="repo-new", last_synced_hash="anchor",
    )
    assert classify_state(snap) == SyncState.REPO_AHEAD


def test_diverged_when_both_moved_off_anchor():
    snap = SyncSnapshot(
        db_hash="db-new", repo_hash="repo-new", last_synced_hash="anchor",
    )
    assert classify_state(snap) == SyncState.DIVERGED


def test_diverged_when_no_anchor_and_unequal():
    """First-ever divergence with no historical anchor — conservative
    classification: assume both sides moved."""
    snap = SyncSnapshot(
        db_hash="db", repo_hash="repo", last_synced_hash="",
    )
    assert classify_state(snap) == SyncState.DIVERGED


# ---- needs_attention -----------------------------------------------


def test_only_in_sync_doesnt_need_attention():
    assert needs_attention(SyncState.IN_SYNC) is False
    assert needs_attention(SyncState.DB_AHEAD) is True
    assert needs_attention(SyncState.REPO_AHEAD) is True
    assert needs_attention(SyncState.DIVERGED) is True


# ---- reconcile event recording -------------------------------------


def test_reconcile_to_repo_advances_anchor_to_repo():
    snap = SyncSnapshot(
        db_hash="anchor", repo_hash="repo-new", last_synced_hash="anchor",
    )
    event = reconcile_to_repo(snap=snap)
    assert event.new_anchor_hash == "repo-new"
    assert event.direction == "repo_to_db"


def test_reconcile_to_db_advances_anchor_to_db():
    snap = SyncSnapshot(
        db_hash="db-new", repo_hash="anchor", last_synced_hash="anchor",
    )
    event = reconcile_to_db(snap=snap)
    assert event.new_anchor_hash == "db-new"
    assert event.direction == "db_to_repo"


def test_reconcile_records_actor_kind():
    snap = SyncSnapshot(
        db_hash="a", repo_hash="b", last_synced_hash="a",
    )
    event = reconcile_to_repo(snap=snap, actor_kind="webhook")
    assert event.actor_kind == "webhook"


# ---- auto-reconcile guard -----------------------------------------


def test_assert_passes_for_simple_states():
    """Only one side moved; auto-reconcile is unambiguous."""
    assert_can_auto_reconcile(SyncState.IN_SYNC)
    assert_can_auto_reconcile(SyncState.DB_AHEAD)
    assert_can_auto_reconcile(SyncState.REPO_AHEAD)


def test_assert_blocks_diverged():
    """DIVERGED needs a human to pick the winner — the workflow
    must fail loudly rather than silently overwriting one side."""
    with pytest.raises(SyncReconcileError, match="DIVERGED"):
        assert_can_auto_reconcile(SyncState.DIVERGED)


# ---- end-to-end common scenarios -----------------------------------


def test_user_edits_in_ui_then_pushes_via_cli():
    """Realistic scenario: app is in_sync, user edits via UI
    (db_ahead), then user runs ``astro app sync`` to push the DB
    state to the repo. After reconcile_to_db, anchor == new db."""
    snap = SyncSnapshot(db_hash="v1", repo_hash="v1", last_synced_hash="v1")
    assert classify_state(snap) == SyncState.IN_SYNC

    # User edits via API → db_hash advances
    edited = SyncSnapshot(db_hash="v2", repo_hash="v1", last_synced_hash="v1")
    assert classify_state(edited) == SyncState.DB_AHEAD

    # Reconcile pushes to repo
    event = reconcile_to_db(snap=edited, actor_kind="user")
    assert event.new_anchor_hash == "v2"
    assert event.direction == "db_to_repo"


def test_developer_pushes_then_webhook_reconciles():
    """Realistic scenario: dev pushes manifest update via git;
    webhook fires and the platform pulls + applies, advancing the
    anchor to the new repo hash."""
    snap = SyncSnapshot(db_hash="v1", repo_hash="v1", last_synced_hash="v1")
    assert classify_state(snap) == SyncState.IN_SYNC

    # Dev push → repo_hash advances
    pushed = SyncSnapshot(db_hash="v1", repo_hash="v2", last_synced_hash="v1")
    assert classify_state(pushed) == SyncState.REPO_AHEAD

    # Webhook reconciles
    event = reconcile_to_repo(snap=pushed, actor_kind="webhook")
    assert event.new_anchor_hash == "v2"


def test_concurrent_edits_create_diverged_state():
    """Realistic scenario: user edits via UI AND dev pushes
    manifest, both happening between two reconciliation cycles."""
    snap = SyncSnapshot(
        db_hash="ui-edit-v2",
        repo_hash="git-push-v2",
        last_synced_hash="v1",
    )
    assert classify_state(snap) == SyncState.DIVERGED

    # Cannot auto-reconcile
    with pytest.raises(SyncReconcileError):
        assert_can_auto_reconcile(classify_state(snap))
