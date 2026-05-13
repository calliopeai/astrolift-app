"""
PR-mode GitOps emission policy (#10, spec 07 §3.4).

When an environment has ``require_approval=True``, the config-repo
emitter opens a pull request instead of pushing directly. Approval
in the platform UI triggers the PR merge; ArgoCD/Flux then sees
the merge on the tracked branch and syncs.

Pure-Python:

* **Branch naming** — deterministic, includes the workflow run id
  so re-firing the same workflow doesn't open a duplicate PR.
* **PR body composer** — links back to the workflow run + the
  Deployment row + an inline diff summary.
* **Approval-event router** — given a platform approval event,
  decide whether to merge or close the corresponding PR.

The actual git push / GitHub API calls live in driver land.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import StrEnum

# Branch naming: ``astrolift/<app_slug>/<env_slug>/<workflow_run_id>``.
# Keeps the namespace clean per app + env; the workflow run id is
# unique even when the same Deployment retries, so we don't open
# duplicate PRs on Temporal restarts.
_SLUG_RE = re.compile(r"[^a-z0-9-]")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")


def branch_name_for(
    *,
    app_slug: str,
    env_slug: str,
    workflow_run_id: str,
) -> str:
    if not workflow_run_id:
        raise ValueError("workflow_run_id is required for branch naming")
    return f"astrolift/{_slugify(app_slug)}/{_slugify(env_slug)}/{_slugify(workflow_run_id)}"


# ---- PR body composer -----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ManifestDiffEntry:
    """One line in the diff summary the emitter computes from the
    rendered manifest set."""

    kind: str
    name: str
    namespace: str
    change: str  # 'create' | 'update' | 'delete'


@dataclasses.dataclass(frozen=True, slots=True)
class PullRequestBody:
    title: str
    body: str


def render_pr_body(
    *,
    app_slug: str,
    env_slug: str,
    deployment_id: int,
    workflow_run_url: str,
    diff_entries: Sequence[ManifestDiffEntry],
) -> PullRequestBody:
    """Compose a deterministic PR title + body. Reviewers should
    see at a glance: which app, which env, what changed."""
    title = f"[astrolift] {app_slug} → {env_slug} (deployment #{deployment_id})"
    summary = (
        f"Astrolift deployment **#{deployment_id}** for `{app_slug}` "
        f"targeting `{env_slug}`.\n\n"
        f"Workflow run: {workflow_run_url}\n\n"
    )
    if diff_entries:
        change_counts: dict[str, int] = {}
        for d in diff_entries:
            change_counts[d.change] = change_counts.get(d.change, 0) + 1
        counts_line = ", ".join(f"{n} {c}" for c, n in sorted(change_counts.items()))
        lines = [
            "### Manifest changes",
            "",
            f"_{counts_line}_",
            "",
            "| Change | Kind | Namespace | Name |",
            "|---|---|---|---|",
        ]
        for d in diff_entries:
            lines.append(f"| {d.change} | {d.kind} | {d.namespace or '—'} | {d.name} |")
        body = summary + "\n".join(lines)
    else:
        body = summary + "_No manifest changes — re-applying the existing snapshot._"
    return PullRequestBody(title=title, body=body)


# ---- approval routing ----------------------------------------------


class ApprovalAction(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PRMergeDecision(StrEnum):
    MERGE = "merge"
    CLOSE = "close"
    NO_OP = "no_op"
    """When the PR was already merged/closed (idempotent re-fire)
    or when the approval is for a non-PR-mode deployment."""


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalDecision:
    decision: PRMergeDecision
    reason: str


def route_approval(
    *,
    action: ApprovalAction,
    pr_state: str,
    pr_mode_enabled: bool,
) -> ApprovalDecision:
    """Decide what to do with the PR when an approval event fires.

    ``pr_state`` is the current state of the PR ('open', 'merged',
    'closed'). ``pr_mode_enabled`` is True only when the env has
    ``require_approval=True``; for non-PR deploys, the approval
    event drives a different code path entirely (this returns
    NO_OP).

    Idempotent: re-firing the same approval (network flake, Temporal
    retry) returns NO_OP rather than retrying the merge/close API
    call — the GitOps backend handles the retry shape.
    """
    if not pr_mode_enabled:
        return ApprovalDecision(
            decision=PRMergeDecision.NO_OP,
            reason="env not in PR mode; approval handled elsewhere",
        )
    if pr_state in ("merged", "closed"):
        return ApprovalDecision(
            decision=PRMergeDecision.NO_OP,
            reason=f"PR already {pr_state}",
        )
    if pr_state != "open":
        raise ValueError(f"unknown PR state {pr_state!r}")

    if action == ApprovalAction.APPROVED:
        return ApprovalDecision(
            decision=PRMergeDecision.MERGE,
            reason="approval granted",
        )
    if action == ApprovalAction.REJECTED:
        return ApprovalDecision(
            decision=PRMergeDecision.CLOSE,
            reason="approval rejected",
        )
    raise ValueError(f"unknown approval action {action!r}")
