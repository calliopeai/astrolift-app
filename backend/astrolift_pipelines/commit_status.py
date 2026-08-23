"""Commit status posting — push pipeline run results back to the SCM (#104).

Astrolift posts a commit status to the source host whenever a pipeline run
triggered by a push or pull_request changes state, so a developer sees the
result on the commit they pushed and branch-protection rules can gate on it.

State mapping
-------------
PipelineRun.Status → SCM state:

  PENDING  / RUNNING  → "pending"
  SUCCESS             → "success"
  FAILURE / TIMED_OUT → "failure"
  CANCELLED           → "error"  ("canceled" on GitLab, which has the state)

Credentials
-----------
Resolution goes through ``connection_resolver`` with
``purpose=ORG_REPO_WRITE``, the same way the workflow-file write and the
webhook install do. This module previously carried its own stubs: a
platform-wide ``settings.PIPELINE_GITHUB_STATUS_TOKEN`` for GitHub and a
GitLab lookup that returned ``None`` unconditionally. Both were wrong in
ways worth naming, because they are what wiring this "cheaply" looks like:

* One token shared across every tenant is a credential that can write to
  orgs other than the one whose run is being reported.
* ``connection_resolver`` exists precisely because near-identical pickers
  each answered "who does this act as" their own way, and one of them
  reached for a *human's* personal OAuth token to authenticate an
  autonomous platform write. Adding a fifth picker here would re-open that.

Design constraints
------------------
* Never raise. A commit status is advisory; failing to post one must not
  fail a run, and must not turn a completion callback into a 500.
* Silent skip when no connection is configured, or when the host has no
  commit-status support here (Bitbucket, Gitea).
* ``context`` is ``astrolift/{pipeline_name}`` so branch-protection rules
  can target it by name.
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

# Hosts this module can post to. Bitbucket and Gitea both have a
# build-status API, but neither has a driver here, and pretending to
# support them would look like "posted" in the logs while nothing lands.
_SUPPORTED_HOSTS: frozenset[str] = frozenset({"github", "gitlab"})


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
# Which host, and which repo on it
# ---------------------------------------------------------------------------


def source_kind_for(pipeline) -> str:
    """Return the SCM host kind for ``pipeline``.

    A bound ``RegisteredApp`` is authoritative: it carries the host the org
    actually onboarded. Falling back to sniffing ``repo_url`` matters
    because pipelines are matched to webhooks *by repo url* and the app
    binding is nullable, so plenty of real pipelines have no app.
    """
    app = getattr(pipeline, "registered_app", None)
    if app is not None and getattr(app, "source_kind", ""):
        return str(app.source_kind)
    host = urllib.parse.urlparse(pipeline.repo_url or "").netloc.lower()
    if not host and ":" in (pipeline.repo_url or ""):
        # git@github.com:owner/repo
        host = (pipeline.repo_url or "").split("@", 1)[-1].split(":", 1)[0].lower()
    for kind in ("github", "gitlab", "bitbucket", "gitea"):
        if kind in host:
            return kind
    return ""


def repo_full_name_for(pipeline) -> str:
    """Return ``owner/repo`` (or ``namespace/project``) for ``pipeline``."""
    app = getattr(pipeline, "registered_app", None)
    if app is not None and getattr(app, "source_repo", ""):
        return str(app.source_repo)
    url = (pipeline.repo_url or "").rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if not url:
        return ""
    if url.startswith("git@") or (":" in url and "//" not in url):
        path = url.split(":", 1)[-1]
    else:
        path = urllib.parse.urlparse(url).path.lstrip("/")
    return path


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
# Payload builders (pure)
# ---------------------------------------------------------------------------


def github_payload_for_run(pipeline_run, *, status: str | None = None, description: str = "") -> dict:
    """Return the GitHub statuses-API request body for ``pipeline_run``.

    Pure, and kept here rather than shared with the deploy payload builder
    in ``astrolift_scm.commit_status`` because the two answer different
    questions: that one describes a deploy to an environment, this one
    describes a pipeline run on a commit.
    """
    pipeline = pipeline_run.pipeline
    if status is None:
        status = _GITHUB_STATE_MAP.get(pipeline_run.status, "error")
    if not description:
        description = _default_description(pipeline_run, status)
    return {
        "state": status,
        "target_url": _run_detail_url(pipeline_run),
        "description": description[:140],  # GitHub caps at 140 chars
        "context": f"astrolift/{pipeline.name}",
    }


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def post_github_commit_status(
    pipeline_run,
    connection,
    *,
    commit_sha: str,
    status: str | None = None,
    description: str = "",
) -> bool:
    """POST a commit status to GitHub for ``pipeline_run``.

    The transport is ``astrolift_scm.commit_status.post_commit_status`` --
    the one GitHub commit-status poster in the codebase, already handling
    orphaned connections, App/OAuth/PAT credentials and GitHub Enterprise.
    Only the payload differs between a deploy status and a run status, so
    only the payload lives here.
    """
    pipeline = pipeline_run.pipeline
    owner, repo = _parse_github_owner_repo(pipeline.repo_url)
    if not owner or not repo:
        full = repo_full_name_for(pipeline)
        owner, _, repo = full.partition("/")
    if not owner or not repo:
        logger.warning(
            "pipeline.commit_status.github.skipped_bad_repo_url pipeline=%s repo_url=%r",
            pipeline.guid,
            pipeline.repo_url,
        )
        return False

    from astrolift_scm.commit_status import post_commit_status

    payload = github_payload_for_run(pipeline_run, status=status, description=description)
    try:
        return post_commit_status(
            connection=connection,
            owner=owner,
            repo=repo,
            sha=commit_sha,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pipeline.commit_status.github post_failed pipeline=%s error=%r",
            pipeline.guid,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def post_gitlab_commit_status(
    pipeline_run,
    connection,
    *,
    commit_sha: str,
    project_id: str | int | None = None,
    status: str | None = None,
) -> bool:
    """POST a commit status to GitLab for ``pipeline_run``.

    GitLab has no poster in ``astrolift_scm`` to delegate to, so the
    transport is here; the credential and the API base still come from the
    resolved connection rather than from a parameter, so there is one
    answer to "who does this act as" per host.
    """
    pipeline = pipeline_run.pipeline

    if status is None:
        status = _GITLAB_STATE_MAP.get(pipeline_run.status, "failed")

    if project_id is None:
        project_id = repo_full_name_for(pipeline)
    if not project_id:
        logger.warning(
            "pipeline.commit_status.gitlab.skipped_no_project pipeline=%s repo_url=%r",
            pipeline.guid,
            pipeline.repo_url,
        )
        return False

    from astrolift_scm.providers.gitlab import GITLAB_API_DEFAULT, _api_base, _token

    try:
        token = _token(connection)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pipeline.commit_status.gitlab.skipped_bad_credential pipeline=%s error=%r",
            pipeline.guid,
            exc,
        )
        return False
    if not token:
        return False

    try:
        base = _api_base(connection)
    except Exception:  # noqa: BLE001
        base = GITLAB_API_DEFAULT

    encoded_project = urllib.parse.quote(str(project_id), safe="")
    url = f"{base.rstrip('/')}/api/v4/projects/{encoded_project}/statuses/{commit_sha}"
    payload: dict = {
        "state": status,
        "name": f"astrolift/{pipeline.name}",
        "target_url": _run_detail_url(pipeline_run),
    }

    return _do_http_post(
        url=url,
        payload=payload,
        headers={
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json",
        },
        label=f"pipeline.commit_status.gitlab pipeline={pipeline.guid}",
    )


# ---------------------------------------------------------------------------
# Dispatch — called on every PipelineRun status change
# ---------------------------------------------------------------------------


def call_commit_status_after_run(pipeline_run, *, commit_sha: str | None = None) -> bool:
    """Post the commit status for ``pipeline_run``'s current status.

    Called on every run status transition, not only terminal ones: a
    branch-protection rule that requires ``astrolift/{pipeline}`` needs to
    see a *pending* status appear, otherwise the check it is waiting on
    never exists and the PR is blocked on nothing.

    Only posts for runs triggered by push or pull_request; schedule,
    manual and API runs have no commit to annotate. Returns True when a
    status was posted, so a caller (or a test) can tell "posted" from
    "deliberately skipped" -- both of which are success here.
    """
    from astrolift_pipelines.models import PipelineRun  # local to avoid cycles

    if pipeline_run.trigger_kind not in (
        PipelineRun.TriggerKind.PUSH,
        PipelineRun.TriggerKind.PULL_REQUEST,
    ):
        return False

    sha = commit_sha or getattr(pipeline_run, "commit_sha", "")
    if not sha:
        # Nothing to annotate. Real for an API-created run that borrowed a
        # push trigger_kind, and not worth a warning.
        logger.info(
            "pipeline.commit_status.skipped_no_sha run=%s",
            pipeline_run.guid,
        )
        return False

    pipeline = pipeline_run.pipeline
    source_kind = source_kind_for(pipeline)
    if source_kind not in _SUPPORTED_HOSTS:
        logger.info(
            "pipeline.commit_status.skipped_unsupported_host pipeline=%s source_kind=%r",
            pipeline.guid,
            source_kind,
        )
        return False

    connection = _resolve_connection(pipeline, source_kind=source_kind)
    if connection is None:
        logger.info(
            "pipeline.commit_status.skipped_no_credential pipeline=%s run=%s source_kind=%s",
            pipeline.guid,
            pipeline_run.guid,
            source_kind,
        )
        return False

    if source_kind == "github":
        return post_github_commit_status(pipeline_run, connection, commit_sha=sha)
    return post_gitlab_commit_status(pipeline_run, connection, commit_sha=sha)


def post_commit_status_for_run(pipeline_run, *, commit_sha: str | None = None) -> bool:
    """Never-raising wrapper for call sites on the run's write path.

    A status transition must land in the database whether or not the SCM
    host is reachable, so every call site uses this rather than calling
    the dispatcher directly.
    """
    try:
        return call_commit_status_after_run(pipeline_run, commit_sha=commit_sha)
    except Exception:  # noqa: BLE001
        logger.warning(
            "pipeline.commit_status.dispatch_failed run=%s",
            getattr(pipeline_run, "guid", "?"),
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Credential lookup
# ---------------------------------------------------------------------------


def _resolve_connection(pipeline, *, source_kind: str):
    """Return the org-level connection that authenticates the post, or None.

    ``ORG_REPO_WRITE``, not ``PLATFORM_REPO_WRITE``: posting a commit
    status is an autonomous platform write under an org identity, but it
    is not a secrets write, so an App-less org that onboarded with an org
    OAuth or PAT connection should still get statuses. Returns None rather
    than raising, because a missing connection is a skip and not an error.
    """
    from astrolift_scm.services.connection_resolver import (
        ORG_REPO_WRITE,
        ConnectionResolutionError,
        resolve_connection,
    )

    try:
        return resolve_connection(
            pipeline.organization_id,
            purpose=ORG_REPO_WRITE,
            source_kind=source_kind,
        )
    except ConnectionResolutionError:
        return None


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------


def _do_http_post(url: str, payload: dict, headers: dict, label: str) -> bool:
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
                return False
            return True
    except urllib.error.HTTPError as exc:
        logger.warning(
            "%s post_failed http_error=%s url=%s",
            label,
            exc.code,
            url,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s post_failed error=%r url=%s",
            label,
            exc,
            url,
        )
        return False


# ---------------------------------------------------------------------------
# Description helpers
# ---------------------------------------------------------------------------


def _default_description(pipeline_run, scm_state: str) -> str:
    """Generate a short description string for the commit status."""
    name = pipeline_run.pipeline.name
    if scm_state == "pending":
        return f"Astrolift: {name} — running"
    if scm_state == "success":
        return f"Astrolift: {name} — passed"
    if scm_state == "failure":
        if pipeline_run.status == "timed_out":
            return f"Astrolift: {name} — timed out"
        return f"Astrolift: {name} — failed"
    return f"Astrolift: {name} — {pipeline_run.status}"
