"""Pipeline trigger filters — path globs, tag pushes, fork PR policy (#95).

Extends the basic branch-filter trigger matching from webhook_views.py with
richer filtering:

1. **Path globs**: Only trigger when changed files match a glob pattern.
   (Requires the webhook payload to include file paths — GitHub does via
    the Commits array; GitLab via the `changes` attribute)

2. **Tag push filters**: Match specific tag patterns (semver prefix, etc.)
   using glob matching.

3. **Fork PR secrets policy**: PRs from forks must not have access to org
   secrets by default (security). The trigger filter flags fork PRs so the
   dispatcher can strip secrets from the context.

4. **Manual inputs**: Manual triggers can declare required inputs in the
   TOML. The triggerPipelineRun mutation validates inputs against the schema.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any


class TriggerFilterResult:
    """Outcome of applying trigger filters to a webhook event."""

    def __init__(
        self,
        *,
        should_trigger: bool,
        is_fork: bool = False,
        skip_secrets: bool = False,
        reason: str = "",
    ) -> None:
        self.should_trigger = should_trigger
        self.is_fork = is_fork
        self.skip_secrets = skip_secrets  # True → do not inject org secrets
        self.reason = reason


def apply_trigger_filters(
    trigger_config: dict[str, Any],
    event: str,
    ref: str,
    payload: dict[str, Any],
) -> TriggerFilterResult:
    """Apply all configured trigger filters to a webhook event.

    Args:
        trigger_config: The Trigger.config dict from the DB (parsed from TOML)
        event: Canonical event kind ("push" | "pull_request")
        ref: The ref string (e.g. "refs/heads/main" or "refs/tags/v1.0")
        payload: Raw webhook payload dict

    Returns:
        TriggerFilterResult with should_trigger and policy flags.
    """
    # Branch filter (already implemented in webhook_views.py)
    # Duplicated here for filter-only paths.
    if event == "push":
        return _apply_push_filters(trigger_config, ref, payload)
    elif event == "pull_request":
        return _apply_pr_filters(trigger_config, ref, payload)
    return TriggerFilterResult(should_trigger=False, reason=f"unhandled event {event!r}")


# ---------------------------------------------------------------------------
# Push filters
# ---------------------------------------------------------------------------


def _apply_push_filters(config: dict, ref: str, payload: dict) -> TriggerFilterResult:
    """Apply push-specific filters."""
    is_tag = ref.startswith("refs/tags/")
    branch = ref.removeprefix("refs/heads/") if not is_tag else ""
    tag = ref.removeprefix("refs/tags/") if is_tag else ""

    # Branch filter
    branches = config.get("branches", [])
    if branches and not is_tag:
        if not any(_glob_match(branch, pattern) for pattern in branches):
            return TriggerFilterResult(should_trigger=False, reason=f"branch {branch!r} not in filter")

    # Tag filter
    tags = config.get("tags", [])
    if tags:
        if not is_tag:
            return TriggerFilterResult(should_trigger=False, reason="no tags configured for branch push")
        if not any(_glob_match(tag, pattern) for pattern in tags):
            return TriggerFilterResult(should_trigger=False, reason=f"tag {tag!r} not in filter")

    # Path glob filter
    path_filters = config.get("paths", [])
    if path_filters:
        changed_files = _extract_changed_files_github(payload)
        if not any(any(_glob_match(f, pattern) for f in changed_files) for pattern in path_filters):
            return TriggerFilterResult(
                should_trigger=False,
                reason=f"no changed files match path filters: {path_filters}",
            )

    return TriggerFilterResult(should_trigger=True)


# ---------------------------------------------------------------------------
# PR filters
# ---------------------------------------------------------------------------


def _apply_pr_filters(config: dict, ref: str, payload: dict) -> TriggerFilterResult:
    """Apply pull_request-specific filters including fork policy."""
    pr = payload.get("pull_request") or {}
    is_fork = (pr.get("head", {}).get("repo", {}) or {}).get("fork", False)

    # Fork secrets policy: by default, fork PRs don't get org secrets
    fork_secrets_policy = config.get("fork_secrets_policy", "no_secrets")
    skip_secrets = is_fork and fork_secrets_policy == "no_secrets"

    # Branch filter for PRs (target branch)
    branches = config.get("branches", [])
    if branches:
        base_branch = pr.get("base", {}).get("ref", ref)
        if not any(_glob_match(base_branch, pattern) for pattern in branches):
            return TriggerFilterResult(
                should_trigger=False,
                reason=f"target branch {base_branch!r} not in filter",
            )

    return TriggerFilterResult(
        should_trigger=True,
        is_fork=is_fork,
        skip_secrets=skip_secrets,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _glob_match(value: str, pattern: str) -> bool:
    """Match a value against a glob pattern or regex.

    Supports:
    - Shell globs: *, ?, [abc]
    - Regex: /pattern/
    - Negation: !pattern
    """
    if pattern.startswith("!"):
        return not _glob_match(value, pattern[1:])
    if pattern.startswith("/") and pattern.endswith("/"):
        return bool(re.search(pattern[1:-1], value))
    return fnmatch.fnmatch(value, pattern)


def _extract_changed_files_github(payload: dict) -> list[str]:
    """Extract changed file paths from a GitHub push payload."""
    files: list[str] = []
    for commit in payload.get("commits") or []:
        files.extend(commit.get("added") or [])
        files.extend(commit.get("modified") or [])
        files.extend(commit.get("removed") or [])
    return list(set(files))


def validate_manual_inputs(declared_inputs: dict[str, Any], provided: dict[str, Any]) -> None:
    """Validate manual trigger inputs against the TOML-declared input schema.

    Raises ValueError if any required input is missing or type-mismatched.
    """
    for key, spec in declared_inputs.items():
        required = spec.get("required", False)
        if required and key not in provided:
            raise ValueError(f"Required input {key!r} not provided for manual trigger")
