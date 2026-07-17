"""
GitHub Deployments API — reflect Astrolift's deploy lifecycle into
GitHub's Environments UI + PR "deployed to <env>" badges (#1124).

Companion to ``commit_status.py`` (which stamps a commit *status*);
this module drives the richer Deployments API:

  - ``create_github_deployment`` → POST /repos/:o/:r/deployments,
    returns the numeric deployment id GitHub assigns.
  - ``post_github_deployment_status`` → POST that deployment's
    /statuses, moving it through in_progress → success | failure.

Both are best-effort: a GitHub outage, a revoked App permission, or a
network blip must NEVER fail or slow a platform deploy. Every call is
bounded (``_TIMEOUT_SECONDS``) and swallows HTTPError / URLError into a
logged warning + None/False return — the caller (the deploy lifecycle)
treats a missing reflection as "GitHub just doesn't show this deploy",
never as an error.

Auth is the org GitHub-App installation token (deployments + statuses
are repo writes; App-preferred, non-expiring). Orphaned connections and
connections without an installation id (OAuth-user / PAT rows) short-
circuit without a call — same policy as ``commit_status.post_commit_status``.
"""

from __future__ import annotations

import json as _json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
# Bounded so a hung GitHub API can never stall a deploy activity
# unboundedly (mirrors github_app.py / pipelines commit_status ~10s).
_TIMEOUT_SECONDS = 10.0
# GitHub caps deployment/description text at 140 chars.
_TEXT_CAP = 140


def _installation_token(connection) -> str | None:
    """Mint the App installation token, best-effort.

    Mirrors ``commit_status``' guard: orphaned connections and
    connections without an installation id (OAuth-user / PAT rows) can't
    drive the Deployments API, so they short-circuit to ``None`` without
    a call. Any minting error (revoked key, unreachable GitHub, wrong
    connection kind) is swallowed to ``None`` — never raised — so the
    deploy is unaffected.
    """
    if connection.is_orphaned or not getattr(connection, "installation_id", ""):
        return None
    try:
        from astrolift_scm.providers import github_app

        return github_app.installation_token(connection) or None
    except Exception:  # noqa: BLE001 — best-effort: any auth failure just skips reflection
        logger.warning("github_deployments: installation token mint failed", exc_info=True)
        return None


def _post(url: str, token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    """POST JSON to GitHub, best-effort.

    Returns ``(status_code, body)`` for any HTTP *response* — including
    4xx/5xx, so the caller can inspect the code — or ``None`` when the
    request never completed (network error, timeout). Never raises.
    """
    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            parsed = _json.loads(raw) if raw else {}
            return resp.getcode(), (parsed if isinstance(parsed, dict) else {})
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 — the diagnostic read is itself best-effort
            pass
        logger.warning("github_deployments: POST %s failed http=%s %s", url, exc.code, detail)
        return exc.code, {}
    except urllib.error.URLError as exc:
        logger.warning("github_deployments: POST %s network error: %s", url, exc.reason)
        return None
    except Exception:  # noqa: BLE001 — defensive: reflection must never raise
        logger.warning("github_deployments: POST %s unexpected error", url, exc_info=True)
        return None


def create_github_deployment(
    connection,
    owner: str,
    repo: str,
    *,
    ref: str,
    environment: str,
    description: str = "",
) -> int | None:
    """Create a GitHub Deployment for ``ref`` in ``environment``.

    ``auto_merge=False`` so GitHub never tries to merge the ref into the
    default branch, and ``required_contexts=[]`` so it doesn't 409 while
    waiting on CI check contexts we don't gate on. Returns the numeric
    deployment id on a 2xx, else ``None`` (best-effort).
    """
    token = _installation_token(connection)
    if not token:
        return None
    url = f"{_API_ROOT}/repos/{owner}/{repo}/deployments"
    payload: dict[str, Any] = {
        "ref": ref,
        "environment": environment,
        "description": (description or "")[:_TEXT_CAP],
        "auto_merge": False,
        "required_contexts": [],
    }
    result = _post(url, token, payload)
    if result is None:
        return None
    code, response_body = result
    if not (200 <= code < 300):
        return None
    dep_id = response_body.get("id")
    return dep_id if isinstance(dep_id, int) else None


def post_github_deployment_status(
    connection,
    owner: str,
    repo: str,
    deployment_id: int,
    *,
    state: str,
    environment: str = "",
    environment_url: str = "",
    log_url: str = "",
    description: str = "",
) -> bool:
    """Move a GitHub Deployment to ``state``.

    ``state`` is one of ``in_progress`` / ``success`` / ``failure`` /
    ``error`` / ``inactive``. ``environment_url`` is the live app URL
    GitHub renders a "View deployment" button for; ``log_url`` deep-links
    back to the Astrolift deploy detail. Returns True on a 2xx, else
    False (best-effort).
    """
    token = _installation_token(connection)
    if not token:
        return False
    url = f"{_API_ROOT}/repos/{owner}/{repo}/deployments/{deployment_id}/statuses"
    payload: dict[str, Any] = {"state": state}
    # Only send the optional link/label fields when populated — GitHub
    # renders an empty environment_url as a broken button.
    if environment:
        payload["environment"] = environment
    if environment_url:
        payload["environment_url"] = environment_url
    if log_url:
        payload["log_url"] = log_url
    if description:
        payload["description"] = description[:_TEXT_CAP]
    result = _post(url, token, payload)
    if result is None:
        return False
    code, _ = result
    return 200 <= code < 300
