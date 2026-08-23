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


def deployment_status_to_github(status: str, *, env_name: str, target_url: str = "") -> dict[str, Any]:
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

    Any org-level connection kind authenticates this, not only an App
    installation. That matters because ``connection_resolver``'s
    ``ORG_REPO_WRITE`` deliberately falls back to an org OAuth-user or PAT
    connection so an App-less org still works; handing such a row to an
    App-only poster resolves a perfectly good credential and then silently
    posts nothing. ``providers.github._token`` is the superset helper --
    it covers PAT, OAuth-user, and App-install -- and it is what every
    other GitHub write in this package already uses.
    """
    if connection.is_orphaned:
        return False
    # Only an App-install row needs an installation id; requiring one of
    # every kind is what made OAuth/PAT orgs silently unpostable.
    if connection.kind == "github_app_install" and not connection.installation_id:
        return False
    # Lazy-import requests so the manifest layer doesn't haul it in
    # for unrelated tests.
    import requests

    from astrolift_scm.providers.github import (
        GITHUB_API_DEFAULT,
        GithubProviderError,
        _token,
    )

    try:
        token = _token(connection)
    except GithubProviderError:
        # An unusable credential is the same outcome as no credential: a
        # commit status is advisory and must never fail the caller.
        return False
    if not token:
        return False
    # Honour api_base_url so this works against GitHub Enterprise, which
    # the hardcoded api.github.com silently could not.
    base = (connection.api_base_url or GITHUB_API_DEFAULT).rstrip("/")
    url = f"{base}/repos/{owner}/{repo}/statuses/{sha}"
    scheme = "Bearer" if connection.kind == "github_app_install" else "token"
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"{scheme} {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )
    except requests.RequestException:
        return False
    return 200 <= resp.status_code < 300
