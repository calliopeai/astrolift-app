"""Tests for PR-mode GitOps emission policy (#10, spec 07 §3.4)."""

from __future__ import annotations

import pytest

from astrolift_clusters.gitops_pr_mode import (
    ApprovalAction,
    ManifestDiffEntry,
    PRMergeDecision,
    branch_name_for,
    render_pr_body,
    route_approval,
)

# ---- branch naming -------------------------------------------------


def test_branch_name_format():
    out = branch_name_for(
        app_slug="api",
        env_slug="prod",
        workflow_run_id="run-abc-123",
    )
    assert out == "astrolift/api/prod/run-abc-123"


def test_branch_name_slugifies_inputs():
    """Slashes / capitals / underscores in inputs become dashes —
    git refs reject most of those."""
    out = branch_name_for(
        app_slug="My App",
        env_slug="prod_canary",
        workflow_run_id="abc/123",
    )
    assert out == "astrolift/my-app/prod-canary/abc-123"


def test_branch_name_requires_workflow_run_id():
    """Without the run id, retries would push duplicate PRs."""
    with pytest.raises(ValueError):
        branch_name_for(app_slug="api", env_slug="prod", workflow_run_id="")


def test_branch_name_deterministic_for_same_run():
    a = branch_name_for(app_slug="api", env_slug="p", workflow_run_id="r1")
    b = branch_name_for(app_slug="api", env_slug="p", workflow_run_id="r1")
    assert a == b


# ---- PR body --------------------------------------------------------


def test_pr_title_includes_app_env_deployment():
    out = render_pr_body(
        app_slug="api",
        env_slug="prod",
        deployment_id=42,
        workflow_run_url="https://platform/wf/123",
        diff_entries=[],
    )
    assert "api" in out.title
    assert "prod" in out.title
    assert "#42" in out.title


def test_pr_body_links_workflow_run():
    out = render_pr_body(
        app_slug="api",
        env_slug="prod",
        deployment_id=42,
        workflow_run_url="https://platform/wf/123",
        diff_entries=[],
    )
    assert "https://platform/wf/123" in out.body


def test_pr_body_with_no_changes_says_so():
    out = render_pr_body(
        app_slug="api",
        env_slug="prod",
        deployment_id=42,
        workflow_run_url="https://x",
        diff_entries=[],
    )
    assert "No manifest changes" in out.body


def test_pr_body_lists_diff_entries():
    diff = [
        ManifestDiffEntry(kind="Deployment", name="api", namespace="acme-api", change="update"),
        ManifestDiffEntry(kind="Service", name="api", namespace="acme-api", change="create"),
        ManifestDiffEntry(kind="ConfigMap", name="legacy", namespace="acme-api", change="delete"),
    ]
    out = render_pr_body(
        app_slug="api",
        env_slug="prod",
        deployment_id=42,
        workflow_run_url="https://x",
        diff_entries=diff,
    )
    # Each kind/name/namespace/change appears in the body table
    for entry in diff:
        assert entry.kind in out.body
        assert entry.name in out.body
        assert entry.change in out.body
    # Counts summary line
    assert "1 create" in out.body
    assert "1 delete" in out.body
    assert "1 update" in out.body


# ---- approval routing ----------------------------------------------


def test_approved_open_pr_merges():
    out = route_approval(
        action=ApprovalAction.APPROVED,
        pr_state="open",
        pr_mode_enabled=True,
    )
    assert out.decision == PRMergeDecision.MERGE


def test_rejected_open_pr_closes():
    out = route_approval(
        action=ApprovalAction.REJECTED,
        pr_state="open",
        pr_mode_enabled=True,
    )
    assert out.decision == PRMergeDecision.CLOSE


def test_no_op_when_pr_mode_disabled():
    """Non-PR-mode envs route approvals through a different code
    path entirely. This module returns NO_OP."""
    out = route_approval(
        action=ApprovalAction.APPROVED,
        pr_state="open",
        pr_mode_enabled=False,
    )
    assert out.decision == PRMergeDecision.NO_OP


def test_no_op_when_already_merged():
    """Idempotent re-fire on Temporal retry."""
    out = route_approval(
        action=ApprovalAction.APPROVED,
        pr_state="merged",
        pr_mode_enabled=True,
    )
    assert out.decision == PRMergeDecision.NO_OP


def test_no_op_when_already_closed():
    out = route_approval(
        action=ApprovalAction.REJECTED,
        pr_state="closed",
        pr_mode_enabled=True,
    )
    assert out.decision == PRMergeDecision.NO_OP


def test_unknown_pr_state_raises():
    """Defensive — surface bad data rather than silently no-op."""
    with pytest.raises(ValueError, match="unknown PR state"):
        route_approval(
            action=ApprovalAction.APPROVED,
            pr_state="frozen",
            pr_mode_enabled=True,
        )
