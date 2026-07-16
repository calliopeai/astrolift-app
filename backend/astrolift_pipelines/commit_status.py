"""Commit status posting — push run results back to GitHub / GitLab (#104).

Astrolift posts a commit status to the source SCM whenever a pipeline run is
triggered by a push or pull_request event.  This allows GitHub / GitLab
branch-protection rules to gate on Astrolift pipeline results.

State mapping
-------------
PipelineRun.Status → SCM state:

  PENDING  / RUNNING  → "pending"
  SUCCESS             → "success"
  FAILURE / TIMED_OUT → "failure"
  CANCELLED           → "error"

Design constraints
------------------
* Never raise — errors are logged and the run is unaffected.
* Silent skip when no credential is configured.
* ``context`` is ``astrolift/{pipeline_name}`` so branch-protection rules can
  target it by name.
* ``target_url`` links to the run detail page in the Astrolift UI.
"""

from __future__ import annotations

import json as _json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Run-status → SCM-state mapping
# ---------------------------------------------------------------------------

_GITHUB_STATE_MAP: dict[str, str] = {
    "pending": "pending",
    "running": "pending",
    "success": "success",
    "failure": "failure",
    "timed_out": "failure",
    "cancelled": "error",
}

_GITLAB_STATE_MAP: dict[str, str] = {
    "pending": "pending",
    "running": "running",
    "success": "success",
    "failure": "failed",
    "timed_out": "failed",
    "cancelled": "canceled",
}


def _run_detail_url(pipeline_run) -> str:
    """Return the Astrolift UI URL for a pipeline run.

    Falls back to an empty string when ASTROLIFT_UI_BASE_URL is not set so
    the status is still posted without a link rather than erroring out.
    """
    try:
        from django.conf import settings

        base = getattr(settings, "ASTROLIFT_UI_BASE_URL", "").rstrip("/")
    except Exception:
        base = ""
    if not base:
        return ""
    pipeline = pipeline_run.pipeline
    return f"{base}/pipelines/{pipeline.guid}/runs/{pipeline_run.guid}"


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def post_github_commit_status(
    pipeline_run,
    github_token: str,
    *,
    commit_sha: str,
    status: str | None = None,
    description: str = "",
) -> None:
    """POST a commit status to GitHub for ``pipeline_run``.

    Parameters
    ----------
    pipeline_run:
        The ``PipelineRun`` model instance.
    github_token:
        A GitHub PAT with ``repo:status`` scope, or a GitHub App
        installation token.
    commit_sha:
        The full 40-character commit SHA to annotate.
    status:
        Override the SCM state string (``"pending"`` / ``"success"`` /
        ``"failure"`` / ``"error"``). When omitted the state is derived
        from ``pipeline_run.status`` via the standard mapping.
    description:
        Short human-readable description shown in the GitHub UI.
        Defaults to a generic description derived from the run status.
    """
    pipeline = pipeline_run.pipeline

    # Derive SCM state from run status when not explicitly provided.
    if status is None:
        status = _GITHUB_STATE_MAP.get(pipeline_run.status, "error")

    if not description:
        description = _default_description(pipeline_run, status)

    # Extract owner/repo from pipeline.repo_url.
    # Expected format: https://github.com/{owner}/{repo}[.git]
    owner, repo = _parse_github_owner_repo(pipeline.repo_url)
    if not owner or not repo:
        logger.warning(
            "pipeline.commit_status.github.skipped_bad_repo_url " "pipeline=%s repo_url=%r",
            pipeline.guid,
            pipeline.repo_url,
        )
        return

    context = f"astrolift/{pipeline.name}"
    target_url = _run_detail_url(pipeline_run)

    payload = {
        "state": status,
        "target_url": target_url,
        "description": description[:140],  # GitHub caps at 140 chars
        "context": context,
    }

    url = f"https://api.github.com/repos/{owner}/{repo}/statuses/{commit_sha}"
    _do_http_post(
        url=url,
        payload=payload,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        label=f"pipeline.commit_status.github pipeline={pipeline.guid}",
    )


def _parse_github_owner_repo(repo_url: str) -> tuple[str, str]:
    """Return (owner, repo) from a GitHub URL, stripping .git suffix."""
    url = repo_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # Accept both https://github.com/owner/repo and git@github.com:owner/repo
    if "github.com" not in url:
        return "", ""
    if url.startswith("git@"):
        # git@github.com:owner/repo
        path = url.split(":", 1)[-1]
    else:
        # https://github.com/owner/repo
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lstrip("/")
    parts = path.split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def post_gitlab_commit_status(
    pipeline_run,
    gitlab_token: str,
    *,
    commit_sha: str,
    project_id: str | int,
    status: str | None = None,
    gitlab_base_url: str = "https://gitlab.com",
) -> None:
    """POST a commit status to GitLab for ``pipeline_run``.

    Parameters
    ----------
    pipeline_run:
        The ``PipelineRun`` model instance.
    gitlab_token:
        A GitLab project access token with ``api`` scope.
    commit_sha:
        The full commit SHA to annotate.
    project_id:
        The GitLab project ID (integer) or URL-encoded namespace/project
        string. Used in the ``/projects/{id}/statuses/{sha}`` endpoint.
    status:
        Override the SCM state string. When omitted the state is derived
        from ``pipeline_run.status``.
    gitlab_base_url:
        GitLab instance base URL. Defaults to ``https://gitlab.com``.
    """
    pipeline = pipeline_run.pipeline

    if status is None:
        status = _GITLAB_STATE_MAP.get(pipeline_run.status, "failed")

    context = f"astrolift/{pipeline.name}"
    target_url = _run_detail_url(pipeline_run)

    # URL-encode the project_id in case it's a namespace/project string.
    encoded_project = urllib.parse.quote(str(project_id), safe="")
    url = f"{gitlab_base_url.rstrip('/')}/api/v4/projects" f"/{encoded_project}/statuses/{commit_sha}"

    payload: dict = {
        "state": status,
        "name": context,
        "target_url": target_url,
    }

    _do_http_post(
        url=url,
        payload=payload,
        headers={
            "PRIVATE-TOKEN": gitlab_token,
            "Content-Type": "application/json",
        },
        label=f"pipeline.commit_status.gitlab pipeline={pipeline.guid}",
    )


# ---------------------------------------------------------------------------
# Dispatch helper — called at PipelineRun terminal state
# ---------------------------------------------------------------------------


def call_commit_status_after_run(pipeline_run, *, commit_sha: str) -> None:
    """Dispatch the correct commit-status post for ``pipeline_run``.

    Called when a ``PipelineRun`` reaches a terminal status (SUCCESS,
    FAILURE, CANCELLED).  Only posts for runs triggered by push or
    pull_request events; skips schedule / manual / API runs silently.

    Credential lookup is best-effort: when no credential is found the
    call logs ``pipeline.commit_status.skipped_no_credential`` and
    returns without affecting the run.
    """
    from astrolift_pipelines.models import PipelineRun  # local to avoid cycles

    trigger_kind = pipeline_run.trigger_kind
    if trigger_kind not in (PipelineRun.TriggerKind.PUSH, PipelineRun.TriggerKind.PULL_REQUEST):
        return

    pipeline = pipeline_run.pipeline

    # Attempt GitHub posting.
    github_token = _get_github_token(pipeline)
    if github_token:
        post_github_commit_status(
            pipeline_run,
            github_token,
            commit_sha=commit_sha,
        )
        return

    # Attempt GitLab posting.
    gitlab_cred = _get_gitlab_credential(pipeline)
    if gitlab_cred:
        post_gitlab_commit_status(
            pipeline_run,
            gitlab_cred["token"],
            commit_sha=commit_sha,
            project_id=gitlab_cred["project_id"],
            gitlab_base_url=gitlab_cred.get("base_url", "https://gitlab.com"),
        )
        return

    logger.info(
        "pipeline.commit_status.skipped_no_credential pipeline=%s run=%s",
        pipeline.guid,
        pipeline_run.guid,
    )


# ---------------------------------------------------------------------------
# Credential lookup (stubs — real implementation wires to ScmCredential model
# once that model is added per the full #104 acceptance criteria)
# ---------------------------------------------------------------------------


def _get_github_token(pipeline) -> str | None:
    """Return a GitHub token for commit-status posting, or None if not set."""
    # TODO(#104): look up ScmCredential for this pipeline with provider=github.
    # For now fall through to env-based override (useful in dev/test).
    try:
        from django.conf import settings

        return getattr(settings, "PIPELINE_GITHUB_STATUS_TOKEN", None) or None
    except Exception:
        return None


def _get_gitlab_credential(pipeline) -> dict | None:
    """Return a dict with gitlab token/project_id/base_url, or None."""
    # TODO(#104): look up ScmCredential for this pipeline with provider=gitlab.
    return None


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------


def _do_http_post(url: str, payload: dict, headers: dict, label: str) -> None:
    """POST JSON payload to ``url``.  Logs errors; never raises."""
    body = _json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code = resp.getcode()
            if status_code not in (200, 201):
                logger.warning(
                    "%s post_failed status=%s url=%s",
                    label,
                    status_code,
                    url,
                )
    except urllib.error.HTTPError as exc:
        logger.warning(
            "%s post_failed http_error=%s url=%s",
            label,
            exc.code,
            url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s post_failed error=%r url=%s",
            label,
            exc,
            url,
        )


# ---------------------------------------------------------------------------
# Description helpers
# ---------------------------------------------------------------------------


def _default_description(pipeline_run, scm_state: str) -> str:
    """Generate a short description string for the commit status."""
    if scm_state == "pending":
        return f"Astrolift: {pipeline_run.pipeline.name} — running"
    if scm_state == "success":
        return f"Astrolift: {pipeline_run.pipeline.name} — passed"
    if scm_state == "failure":
        run_status = pipeline_run.status
        if run_status == "timed_out":
            return f"Astrolift: {pipeline_run.pipeline.name} — timed out"
        return f"Astrolift: {pipeline_run.pipeline.name} — failed"
    return f"Astrolift: {pipeline_run.pipeline.name} — {pipeline_run.status}"
