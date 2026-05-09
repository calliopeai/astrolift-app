"""
GitHub commit-status feedback for deploys (#145).

Two halves:
- ``deployment_status_to_github(status, *, env_name)`` — pure
  mapping from platform Deployment.Status to a GitHub commit-status
  payload (``state``, ``description``, ``context``). The activity
  (in astrolift_workflows.activities) wraps this with the actual
  HTTP POST.
- ``post_commit_status(connection, owner, repo, sha, payload)`` —
  outbound call to GitHub's `/repos/:o/:r/statuses/:sha` endpoint.
  Imports requests lazily and respects the connection's
  ``is_orphaned`` flag (orphaned connections refuse to post; the
  status quietly skips so a dying integration doesn't spam errors).
"""

from __future__ import annotations

from typing import Any

# Maps platform deploy status → GitHub commit-status state. GitHub
# accepts: error, failure, pending, success.
_STATE_MAP: dict[str, str] = {
    "pending_approval": "pending",
    "pending": "pending",
    "deploying": "pending",
    "redeploying": "pending",
    "running": "success",
    "failed": "failure",
    "rolled_back": "failure",
    "superseded": "success",  # superseded means a newer deploy took over
}


# The "context" field shows up in the GitHub UI as the check name.
# Stable across env so users with branch-protection rules can require
# astrolift/<env_name>.
def _context(env_name: str) -> str:
    return f"astrolift/{env_name}"


def _description(status: str, env_name: str) -> str:
    """Human-readable status line shown in the GitHub UI."""
    if status == "running":
        return f"deploy succeeded on {env_name}"
    if status == "failed":
        return f"deploy failed on {env_name}"
    if status == "rolled_back":
        return f"rolled back on {env_name}"
    if status == "superseded":
        return f"superseded by a newer deploy on {env_name}"
    if status in ("pending_approval", "pending"):
        return f"awaiting deploy on {env_name}"
    if status in ("deploying", "redeploying"):
        return f"deploying to {env_name}"
    return f"{status} on {env_name}"


def deployment_status_to_github(
    status: str, *, env_name: str, target_url: str = ""
) -> dict[str, Any]:
    """Pure: returns the GitHub statuses-API request body.

    Caller plugs in the SHA + repo coords + auth and POSTs.
    Unknown statuses fall through to ``error`` so a misconfigured
    workflow shows up in the GitHub UI instead of being silent.
    """
    state = _STATE_MAP.get(status, "error")
    body: dict[str, Any] = {
        "state": state,
        "context": _context(env_name),
        "description": _description(status, env_name),
    }
    if target_url:
        body["target_url"] = target_url
    return body


def post_commit_status(
    *,
    connection,
    owner: str,
    repo: str,
    sha: str,
    payload: dict[str, Any],
    timeout: float = 5.0,
) -> bool:
    """POST the payload to GitHub. Returns True on a 2xx, False
    otherwise. Orphaned connections short-circuit to False without
    making the call (we don't want to keep hitting an installation
    that's been uninstalled).

    The activity layer wraps this so retries + rate-limit handling
    live in the workflow controller, not here.
    """
    if connection.is_orphaned or not connection.installation_id:
        return False
    # Lazy-import requests so the manifest layer doesn't haul it in
    # for unrelated tests.
    import requests

    from astrolift_scm.providers import github_app

    token = github_app.installation_token(connection)
    if not token:
        return False
    url = f"https://api.github.com/repos/{owner}/{repo}/statuses/{sha}"
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )
    except requests.RequestException:
        return False
    return 200 <= resp.status_code < 300
