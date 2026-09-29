r"""
GitHub PR webhook dispatch policy (#83, spec 18 §5-7).

Pure-Python policy. The ``/api/webhooks/github/`` view consults
this module for:

* **Event classification** — GitHub sends a stream of action
  values (\`opened\`, \`reopened\`, \`synchronize\`, \`closed\`,
  \`edited\`, \`labeled\`, ...); we care about a small subset
  and need to refuse the rest cleanly.
* **Idempotency keys** — GitHub re-delivers; the dispatcher
  computes a deterministic workflow ID so duplicate deliveries
  don't fan out duplicate workflows.
* **Eligibility** — apps without \`preview.enabled=true\` are
  ignored; bots-as-PR-authors flag.

Pairs with #93 (signature verification at the ingress) and
#85/#87 (Build/Teardown preview workflows). HMAC validation is
NOT here — that's owned by webhook_ingress.py and runs before
this module is reached.
"""

from __future__ import annotations

import dataclasses
import hashlib
from enum import StrEnum


class GitHubDispatchError(ValueError):
    pass


# ---- event classification ------------------------------------------


class PrAction(StrEnum):
    """Spec 18 §5: the actions we route on. Anything else is
    classified as IGNORED and the handler returns 200 with
    'no-op' so GitHub stops retrying."""

    OPENED = "opened"
    REOPENED = "reopened"
    SYNCHRONIZE = "synchronize"
    """A new push to the PR branch."""

    CLOSED = "closed"
    """May be merged or just closed; merged_at decides."""

    IGNORED = "_ignored"


# Strict alias map — anything not in here becomes IGNORED.
_ACTION_ALIASES = {
    "opened": PrAction.OPENED,
    "reopened": PrAction.REOPENED,
    "synchronize": PrAction.SYNCHRONIZE,
    "closed": PrAction.CLOSED,
}


def classify_action(*, raw_action: str) -> PrAction:
    r"""Map GitHub's \`action\` string to our enum. Unknown
    actions (\`labeled\`, \`edited\`, etc.) classify as IGNORED
    so the handler can return 200 without dispatching."""
    return _ACTION_ALIASES.get(raw_action, PrAction.IGNORED)


# ---- dispatch decision ---------------------------------------------


class DispatchKind(StrEnum):
    """What the webhook should do with this event."""

    BUILD_PREVIEW = "build_preview"
    """Spec §5: opened / reopened / synchronize all map to a
    BuildPreviewWorkflow start."""

    TEARDOWN_PREVIEW = "teardown_preview"
    """Spec §7: closed (whether merged or not) maps to teardown."""

    IGNORE = "ignore"
    """No-op: app doesn't have preview enabled, action isn't a
    preview trigger, or PR was closed without ever having one."""


@dataclasses.dataclass(frozen=True, slots=True)
class PrEventContext:
    """The minimum projection of a GitHub PR webhook payload the
    dispatcher needs."""

    raw_action: str
    repo_full_name: str
    """e.g. ``acme/api``."""

    pr_number: int
    head_sha: str
    head_branch: str
    is_merge: bool
    """True if action=closed AND merged_at is set."""

    is_bot_author: bool
    """True if PR was opened by a bot (GitHub Apps, dependabot,
    renovate). Operators may opt out of building previews for
    bot-authored PRs."""

    author_login: str = ""
    """The PR author's login, kept on the preview it opens (#2155)."""


@dataclasses.dataclass(frozen=True, slots=True)
class AppPreviewContext:
    """Minimum projection of the matched RegisteredApp."""

    registered_app_id: int
    preview_enabled: bool
    """If False, ignore the event regardless of action."""

    skip_bot_authors: bool = False
    """Operator policy: skip bot-authored PRs to save preview
    budget. Default off (most teams want previews on dependency
    bumps)."""


@dataclasses.dataclass(frozen=True, slots=True)
class DispatchDecision:
    r"""Output of \`decide_dispatch\`. Either a dispatch with kind +
    workflow_id, or an IGNORE with a reason."""

    kind: DispatchKind
    workflow_id: str
    r"""Deterministic ID — duplicate deliveries land on the same
    workflow so Temporal de-dupes naturally. Empty string when
    \`kind == IGNORE\`."""

    reason: str
    """Human-readable for handler logs + 200 response body."""


def workflow_id_for_event(
    *,
    kind: DispatchKind,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
) -> str:
    """Deterministic workflow ID. Spec acceptance: 'duplicate
    deliveries don't create duplicate workflows.'

    For BUILD_PREVIEW: the SHA is part of the ID, so a new push
    (synchronize event with new SHA) gets a NEW workflow — the
    previous one's still running gets superseded by the
    activity-side reconcile, but the workflow IDs differ.

    For TEARDOWN_PREVIEW: SHA is omitted (PR has only one
    teardown regardless of which SHA was last deployed).
    """
    if kind == DispatchKind.BUILD_PREVIEW:
        digest = hashlib.sha256(
            f"{repo_full_name}|{pr_number}|{head_sha}".encode(),
        ).hexdigest()[:16]
        return f"build-preview-{repo_full_name.replace('/', '-')}-{pr_number}-{digest}"
    if kind == DispatchKind.TEARDOWN_PREVIEW:
        return f"teardown-preview-{repo_full_name.replace('/', '-')}-{pr_number}"
    return ""


def decide_dispatch(
    *,
    event: PrEventContext,
    app_context: AppPreviewContext,
) -> DispatchDecision:
    """Decide what to do with a GitHub PR webhook event."""
    action = classify_action(raw_action=event.raw_action)

    if action == PrAction.IGNORED:
        return DispatchDecision(
            kind=DispatchKind.IGNORE,
            workflow_id="",
            reason=(f"action {event.raw_action!r} not a preview trigger"),
        )

    if not app_context.preview_enabled:
        return DispatchDecision(
            kind=DispatchKind.IGNORE,
            workflow_id="",
            reason=(f"app {app_context.registered_app_id} does not have preview environments enabled"),
        )

    if action == PrAction.CLOSED:
        return DispatchDecision(
            kind=DispatchKind.TEARDOWN_PREVIEW,
            workflow_id=workflow_id_for_event(
                kind=DispatchKind.TEARDOWN_PREVIEW,
                repo_full_name=event.repo_full_name,
                pr_number=event.pr_number,
                head_sha=event.head_sha,
            ),
            reason=(f"PR #{event.pr_number} {'merged' if event.is_merge else 'closed'}"),
        )

    # opened / reopened / synchronize all build
    if event.is_bot_author and app_context.skip_bot_authors:
        return DispatchDecision(
            kind=DispatchKind.IGNORE,
            workflow_id="",
            reason=(f"PR #{event.pr_number} is bot-authored and app policy skips bot-authored PRs"),
        )

    if not event.head_sha:
        # GitHub edge case: opened webhook arriving before HEAD
        # is populated. Refuse — synchronize will retrigger.
        raise GitHubDispatchError(
            f"PR #{event.pr_number} has no head_sha; can't dispatch build_preview workflow"
        )

    return DispatchDecision(
        kind=DispatchKind.BUILD_PREVIEW,
        workflow_id=workflow_id_for_event(
            kind=DispatchKind.BUILD_PREVIEW,
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            head_sha=event.head_sha,
        ),
        reason=(f"PR #{event.pr_number} {action.value} at {event.head_sha[:8]}"),
    )


# ---- delivery-id idempotency (handler layer) -----------------------


def is_replay(*, delivery_id: str, seen_lookup) -> bool:
    """GitHub also sends X-GitHub-Delivery for retry detection.
    Handler records delivery_id in a dedupe table after
    successful dispatch so the retry returns 200 fast without
    re-running classification.

    ``seen_lookup``: callable(delivery_id) -> bool.
    """
    if not delivery_id:
        return False
    return seen_lookup(delivery_id)
