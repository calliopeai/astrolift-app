"""Tests for GitOps emitter policy (#7, spec 07 §3.1+§3.3)."""

from __future__ import annotations

import pytest

from astrolift_clusters.gitops_emitter import (
    CommitVerb,
    GitopsEmitterError,
    bot_author,
    build_commit_message,
    plan_commit,
    repo_path_for,
)

# ---- repo path layout ----------------------------------------------


def test_repo_path_format_matches_spec():
    """Spec 07 §3.1: clusters/<cluster>/apps/<org>-<app>/<env>/."""
    out = repo_path_for(
        cluster_slug="prod-us-east", org_slug="acme",
        app_slug="api", env_slug="prod",
    )
    assert out == "clusters/prod-us-east/apps/acme-api/prod"


def test_repo_path_rejects_empty_slug():
    with pytest.raises(GitopsEmitterError, match="cluster_slug"):
        repo_path_for(
            cluster_slug="", org_slug="acme",
            app_slug="api", env_slug="prod",
        )


@pytest.mark.parametrize("bad_slug", [
    "Acme",         # uppercase
    "1org",         # leading digit
    "-org",         # leading dash
    "org-",         # trailing dash
    "org_name",     # underscore
    "org.name",     # dot
    "org name",     # space
    "../etc",       # path traversal
])
def test_repo_path_rejects_unsafe_slugs(bad_slug):
    """Defense-in-depth: invalid slugs would either break the git
    path or escape the namespace. Reject at the policy boundary."""
    with pytest.raises(GitopsEmitterError):
        repo_path_for(
            cluster_slug="ok", org_slug=bad_slug,
            app_slug="api", env_slug="prod",
        )


def test_repo_path_accepts_single_char_slug():
    """Edge case: 1-char slugs are technically valid DNS labels."""
    out = repo_path_for(
        cluster_slug="x", org_slug="y",
        app_slug="z", env_slug="p",
    )
    assert out == "clusters/x/apps/y-z/p"


# ---- commit message ------------------------------------------------


def test_commit_title_format():
    """Spec 07 §3.3: '<verb> <app>/<env>: <reason>'."""
    msg = build_commit_message(
        verb=CommitVerb.DEPLOY,
        app_slug="api", env_slug="prod",
        reason="ship #142 (image abc1234)",
        workflow_run_id="wf-123",
    )
    assert msg.title == "deploy api/prod: ship #142 (image abc1234)"


def test_commit_body_includes_workflow_run_trailer():
    """Trailer format lets git log --grep + tooling find commits
    by workflow run id."""
    msg = build_commit_message(
        verb=CommitVerb.DEPLOY,
        app_slug="api", env_slug="prod",
        reason="x",
        workflow_run_id="wf-123-abc",
    )
    assert "Astrolift-WorkflowRun: wf-123-abc" in msg.body


def test_commit_body_includes_deployment_id_when_set():
    msg = build_commit_message(
        verb=CommitVerb.DEPLOY,
        app_slug="api", env_slug="prod",
        reason="x",
        workflow_run_id="wf-1",
        deployment_id=42,
    )
    assert "Astrolift-Deployment: 42" in msg.body


def test_commit_full_concatenates_title_and_body():
    msg = build_commit_message(
        verb=CommitVerb.DEPLOY,
        app_slug="api", env_slug="prod",
        reason="ship",
        workflow_run_id="wf-1",
    )
    assert msg.full.startswith("deploy api/prod: ship")
    assert "Astrolift-WorkflowRun" in msg.full


def test_commit_rejects_empty_reason():
    with pytest.raises(GitopsEmitterError, match="reason"):
        build_commit_message(
            verb=CommitVerb.DEPLOY,
            app_slug="api", env_slug="prod",
            reason="",
            workflow_run_id="wf-1",
        )


def test_commit_rejects_empty_workflow_run_id():
    """Audit trail invariant: every bot commit must trace back."""
    with pytest.raises(GitopsEmitterError, match="workflow_run_id"):
        build_commit_message(
            verb=CommitVerb.DEPLOY,
            app_slug="api", env_slug="prod",
            reason="x",
            workflow_run_id="",
        )


@pytest.mark.parametrize("verb", list(CommitVerb))
def test_every_verb_produces_valid_message(verb):
    msg = build_commit_message(
        verb=verb,
        app_slug="api", env_slug="prod",
        reason="r", workflow_run_id="wf",
    )
    assert msg.title.startswith(verb.value + " ")


# ---- author identity ----------------------------------------------


def test_bot_author_format():
    """Distinct from human users so operators see at-a-glance
    what's bot-authored in git log."""
    name, email = bot_author(platform_domain="acme.platform")
    assert name == "astrolift-bot"
    assert email == "bot@acme.platform"


def test_bot_author_rejects_empty_domain():
    with pytest.raises(GitopsEmitterError):
        bot_author(platform_domain="")


# ---- plan_commit ---------------------------------------------------


def test_plan_commit_bundles_everything():
    plan = plan_commit(
        cluster_slug="prod-us",
        org_slug="acme", app_slug="api", env_slug="prod",
        files=[("manifests.yaml", "kind: Deployment")],
        verb=CommitVerb.DEPLOY,
        reason="ship #142",
        workflow_run_id="wf-1",
        platform_domain="acme.platform",
        deployment_id=142,
    )
    assert plan.repo_path == "clusters/prod-us/apps/acme-api/prod"
    assert plan.message.title == "deploy api/prod: ship #142"
    assert plan.author_name == "astrolift-bot"
    assert plan.author_email == "bot@acme.platform"
    assert "Astrolift-Deployment: 142" in plan.message.body
    assert len(plan.files) == 1


def test_plan_commit_validation_bubbles_up():
    """Bad inputs raise here, not deep in the git ops step."""
    with pytest.raises(GitopsEmitterError):
        plan_commit(
            cluster_slug="../etc",  # bad slug
            org_slug="acme", app_slug="api", env_slug="prod",
            files=[],
            verb=CommitVerb.DEPLOY,
            reason="x", workflow_run_id="wf",
            platform_domain="x.com",
        )
