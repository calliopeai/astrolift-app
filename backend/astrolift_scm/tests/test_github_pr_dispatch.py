"""Tests for GitHub PR webhook dispatch (#83, spec 18 §5-7)."""

from __future__ import annotations

import pytest

from astrolift_scm.github_pr_dispatch import (
    AppPreviewContext,
    DispatchKind,
    GitHubDispatchError,
    PrAction,
    PrEventContext,
    classify_action,
    decide_dispatch,
    is_replay,
    workflow_id_for_event,
)

# ---- action classification -----------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("opened", PrAction.OPENED),
        ("reopened", PrAction.REOPENED),
        ("synchronize", PrAction.SYNCHRONIZE),
        ("closed", PrAction.CLOSED),
    ],
)
def test_classify_known_actions(raw, expected):
    assert classify_action(raw_action=raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "labeled",
        "unlabeled",
        "edited",
        "review_requested",
        "ready_for_review",
        "converted_to_draft",
        "",
    ],
)
def test_classify_unknown_actions_become_ignored(raw):
    """Spec: anything not in our action list is IGNORED so the
    handler returns 200 (GitHub stops retrying)."""
    assert classify_action(raw_action=raw) == PrAction.IGNORED


# ---- workflow ID determinism ---------------------------------------


def test_build_workflow_id_includes_sha():
    """Synchronize with new SHA → new workflow ID. Lets Temporal
    differentiate the two builds."""
    a = workflow_id_for_event(
        kind=DispatchKind.BUILD_PREVIEW,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="aaaa1111",
    )
    b = workflow_id_for_event(
        kind=DispatchKind.BUILD_PREVIEW,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="bbbb2222",
    )
    assert a != b


def test_build_workflow_id_stable_for_same_sha():
    """Duplicate webhook delivery for same SHA → same workflow ID,
    so Temporal de-dupes naturally."""
    a = workflow_id_for_event(
        kind=DispatchKind.BUILD_PREVIEW,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="aaaa1111",
    )
    b = workflow_id_for_event(
        kind=DispatchKind.BUILD_PREVIEW,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="aaaa1111",
    )
    assert a == b


def test_teardown_workflow_id_omits_sha():
    """Teardown is per-PR not per-SHA. Different head_sha values
    map to the same teardown workflow."""
    a = workflow_id_for_event(
        kind=DispatchKind.TEARDOWN_PREVIEW,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="aaaa1111",
    )
    b = workflow_id_for_event(
        kind=DispatchKind.TEARDOWN_PREVIEW,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="bbbb2222",
    )
    assert a == b


def test_workflow_id_includes_repo_in_name():
    """Different repos with same PR number get distinct IDs."""
    a = workflow_id_for_event(
        kind=DispatchKind.BUILD_PREVIEW,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="x",
    )
    b = workflow_id_for_event(
        kind=DispatchKind.BUILD_PREVIEW,
        repo_full_name="globex/api",
        pr_number=42,
        head_sha="x",
    )
    assert a != b
    assert "acme-api" in a
    assert "globex-api" in b


# ---- decide_dispatch -----------------------------------------------


def _event(**overrides) -> PrEventContext:
    base = {
        "raw_action": "opened",
        "repo_full_name": "acme/api",
        "pr_number": 42,
        "head_sha": "aaaa1111",
        "head_branch": "feat/x",
        "is_merge": False,
        "is_bot_author": False,
    }
    base.update(overrides)
    return PrEventContext(**base)


def _app(**overrides) -> AppPreviewContext:
    base = {"registered_app_id": 1, "preview_enabled": True}
    base.update(overrides)
    return AppPreviewContext(**base)


def test_dispatch_opened_builds():
    decision = decide_dispatch(event=_event(raw_action="opened"), app_context=_app())
    assert decision.kind == DispatchKind.BUILD_PREVIEW
    assert "build-preview" in decision.workflow_id


def test_dispatch_synchronize_builds():
    decision = decide_dispatch(
        event=_event(raw_action="synchronize"),
        app_context=_app(),
    )
    assert decision.kind == DispatchKind.BUILD_PREVIEW


def test_dispatch_reopened_builds():
    decision = decide_dispatch(
        event=_event(raw_action="reopened"),
        app_context=_app(),
    )
    assert decision.kind == DispatchKind.BUILD_PREVIEW


def test_dispatch_closed_tears_down():
    decision = decide_dispatch(
        event=_event(raw_action="closed", is_merge=False),
        app_context=_app(),
    )
    assert decision.kind == DispatchKind.TEARDOWN_PREVIEW
    assert "closed" in decision.reason


def test_dispatch_merged_tears_down():
    decision = decide_dispatch(
        event=_event(raw_action="closed", is_merge=True),
        app_context=_app(),
    )
    assert decision.kind == DispatchKind.TEARDOWN_PREVIEW
    assert "merged" in decision.reason


def test_dispatch_ignores_other_actions():
    decision = decide_dispatch(
        event=_event(raw_action="labeled"),
        app_context=_app(),
    )
    assert decision.kind == DispatchKind.IGNORE


def test_dispatch_ignores_disabled_preview():
    """Spec: apps without preview.enabled=true are ignored."""
    decision = decide_dispatch(
        event=_event(raw_action="opened"),
        app_context=_app(preview_enabled=False),
    )
    assert decision.kind == DispatchKind.IGNORE
    assert "preview" in decision.reason.lower()


def test_dispatch_skips_bot_when_policy_set():
    """Operator opt-in: skip bot-authored PRs to save budget."""
    decision = decide_dispatch(
        event=_event(is_bot_author=True),
        app_context=_app(skip_bot_authors=True),
    )
    assert decision.kind == DispatchKind.IGNORE
    assert "bot" in decision.reason.lower()


def test_dispatch_runs_for_bot_when_policy_off():
    """Default: bot-authored PRs do build (most teams want
    dependabot/renovate previews)."""
    decision = decide_dispatch(
        event=_event(is_bot_author=True),
        app_context=_app(skip_bot_authors=False),
    )
    assert decision.kind == DispatchKind.BUILD_PREVIEW


def test_dispatch_refuses_missing_head_sha():
    """GitHub edge case: opened arriving before HEAD populated."""
    with pytest.raises(GitHubDispatchError, match="head_sha"):
        decide_dispatch(
            event=_event(head_sha=""),
            app_context=_app(),
        )


def test_dispatch_closed_does_not_need_head_sha():
    """Teardown doesn't need head_sha — workflow ID omits it."""
    decision = decide_dispatch(
        event=_event(raw_action="closed", head_sha=""),
        app_context=_app(),
    )
    assert decision.kind == DispatchKind.TEARDOWN_PREVIEW


# ---- delivery-id replay --------------------------------------------


def test_is_replay_true_when_seen():
    assert (
        is_replay(
            delivery_id="abc",
            seen_lookup=lambda d: d == "abc",
        )
        is True
    )


def test_is_replay_false_when_unseen():
    assert (
        is_replay(
            delivery_id="abc",
            seen_lookup=lambda d: False,
        )
        is False
    )


def test_is_replay_false_when_no_id():
    """Some test fixtures lack delivery_id; treat as not-replay so
    the dispatch proceeds rather than silently no-op."""
    assert (
        is_replay(
            delivery_id="",
            seen_lookup=lambda d: True,
        )
        is False
    )
