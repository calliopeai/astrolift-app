"""The ``github_checks`` stage contract and its pass/fail rule (#2489).

A ``github_checks`` stage waits for named GitHub check runs on a pull
request's head commit. Its configuration is the stage's kind-specific
``iteration`` object::

    {"repo": "owner/name", "sha_source": "pull_request_head",
     "pull_request_path": "pr", "checks": ["lint", "test"]}

``pull_request_path`` is a dotted field in the preceding stage's output (the
run input for a first stage) holding a pull request number or that
repository's pull request URL. Everything here is pure, so the workflow
sandbox, the authoring paths and the poll activity share one rule.
"""

from __future__ import annotations

import re
from typing import Any

SHA_SOURCE_PULL_REQUEST_HEAD = "pull_request_head"
MAX_REQUIRED_CHECKS = 20
MAX_CHECK_NAME_LENGTH = 255
MAX_RUNS_PER_CHECK = 10
MAX_PULL_REQUEST = 2_147_483_647

# A required check passes on the conclusions GitHub branch protection accepts.
PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

PENDING = "pending"
PASSED = "passed"
FAILED = "failed"
TIMED_OUT = "timed_out"
ERROR = "error"
TERMINAL_OUTCOMES = frozenset({PASSED, FAILED, TIMED_OUT, ERROR})

_KEYS = frozenset({"repo", "sha_source", "pull_request_path", "checks"})
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}\Z")
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}\Z")
_NUMBER = re.compile(r"[1-9][0-9]{0,9}\Z")
_PULL_URL = re.compile(r"https?://[^/\s?#]+/([^/\s?#]+)/([^/\s?#]+)/pull/([1-9][0-9]{0,9})(?:[/?#]\S*)?\Z")


class GithubChecksContractError(ValueError):
    pass


def validate_github_checks(value: Any) -> dict:
    """Return the normalized gate configuration or refuse it."""
    if not isinstance(value, dict) or set(value) != _KEYS:
        raise GithubChecksContractError(
            "github_checks requires exactly repo, sha_source, pull_request_path and checks"
        )
    repo = value["repo"]
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo.split("/")[1] in {".", ".."}:
        raise GithubChecksContractError("github_checks repo must be a GitHub owner/name repository")
    if value["sha_source"] != SHA_SOURCE_PULL_REQUEST_HEAD:
        raise GithubChecksContractError(f'github_checks sha_source must be "{SHA_SOURCE_PULL_REQUEST_HEAD}"')
    path = value["pull_request_path"]
    if not isinstance(path, str) or not _PATH.fullmatch(path):
        raise GithubChecksContractError(
            "github_checks pull_request_path must be a dotted field path in the preceding output"
        )
    checks = value["checks"]
    if (
        not isinstance(checks, list)
        or not 1 <= len(checks) <= MAX_REQUIRED_CHECKS
        or any(
            not isinstance(name, str) or not name or name != name.strip() or len(name) > MAX_CHECK_NAME_LENGTH
            for name in checks
        )
        or len(set(checks)) != len(checks)
    ):
        raise GithubChecksContractError(
            f"github_checks checks must list 1 to {MAX_REQUIRED_CHECKS} distinct check names "
            "without surrounding whitespace"
        )
    return {
        "repo": repo,
        "sha_source": SHA_SOURCE_PULL_REQUEST_HEAD,
        "pull_request_path": path,
        "checks": list(checks),
    }


def pull_request_number(config: dict, previous: Any) -> int:
    """Read the gated pull request from the preceding output."""
    path = config["pull_request_path"]
    value = previous
    for field in path.split("."):
        if not isinstance(value, dict) or field not in value:
            raise GithubChecksContractError(f"the preceding output has no pull request at {path}")
        value = value[field]
    number = None
    if type(value) is int:
        number = value
    elif isinstance(value, str):
        text = value.strip()
        match = _PULL_URL.fullmatch(text)
        if _NUMBER.fullmatch(text):
            number = int(text)
        elif match and f"{match[1]}/{match[2]}".lower() == config["repo"].lower():
            number = int(match[3])
    if number is None or not 1 <= number <= MAX_PULL_REQUEST:
        raise GithubChecksContractError(
            f"{path} must hold a pull request number or a pull request URL of {config['repo']}"
        )
    return number


def _text(value: Any) -> str | None:
    return value[:500] if isinstance(value, str) else None


def _run_state(run: dict) -> str:
    if run.get("status") != "completed":
        return PENDING
    return PASSED if run.get("conclusion") in PASSING_CONCLUSIONS else FAILED


def _run_record(run: dict) -> dict:
    app = run.get("app")
    return {
        "id": run["id"] if type(run.get("id")) is int else None,
        "status": _text(run.get("status")),
        "conclusion": _text(run.get("conclusion")),
        "app": _text(app.get("slug")) if isinstance(app, dict) else None,
        "url": _text(run.get("html_url")),
    }


def evaluate_check_runs(required: list[str], check_runs: list) -> dict:
    """Decide the required checks from a commit's check runs.

    Every run of a required name counts, so a passing run reported under the
    same name cannot hide a failing one. A name with no run yet is
    ``missing`` and keeps the gate pending.
    """
    by_name: dict[str, list[dict]] = {name: [] for name in required}
    for run in check_runs:
        name = run.get("name") if isinstance(run, dict) else None
        if isinstance(name, str) and name in by_name:
            by_name[name].append(run)
    checks = []
    for name in required:
        runs = by_name[name]
        states = {_run_state(run) for run in runs}
        if not runs:
            state = "missing"
        elif FAILED in states:
            state = FAILED
        elif PENDING in states:
            state = PENDING
        else:
            state = PASSED
        checks.append(
            {"name": name, "state": state, "runs": [_run_record(run) for run in runs[:MAX_RUNS_PER_CHECK]]}
        )
    if any(check["state"] == FAILED for check in checks):
        outcome = FAILED
    elif all(check["state"] == PASSED for check in checks):
        outcome = PASSED
    else:
        outcome = PENDING
    return {"outcome": outcome, "checks": checks}


def outcome_message(result: dict) -> str:
    """The stage execution's error message for a gate that did not pass."""
    checks = result.get("checks") or []
    if result["outcome"] == FAILED:
        names = ", ".join(check["name"] for check in checks if check["state"] == FAILED)
        return f"required GitHub checks failed: {names}"
    if result["outcome"] == TIMED_OUT:
        waiting = ", ".join(
            f"{check['name']} ({check['state']})" for check in checks if check["state"] != PASSED
        )
        message = "required GitHub checks did not pass before the stage timeout"
        if waiting:
            message += f": {waiting}"
        if result.get("error"):
            message += f"; last error: {result['error']}"
        return message
    return str(result.get("error") or "the GitHub checks gate could not be evaluated")
