"""Workflow-dispatch through a stored SourceConnection (#387).

Fires the source host's "manually trigger a workflow run" API for an
app's CI workflow file. Lets an operator rebuild + redeploy from
HEAD of the configured deploy branch without round-tripping through
``git push`` — useful when the registry image is stale but the source
hasn't moved.

GitHub Actions is the only host that exposes a clean workflow-
dispatch primitive today. GitLab pipelines and Bitbucket builds both
require an authenticated POST against a *named* pipeline trigger token
that the operator pre-configures on the host — those paths raise
``NotImplementedError`` until we wire the per-host trigger-token model.

The "workflow file missing" case is distinguished from generic 404s
so the resolver can surface a targeted "Sync your CI workflow to the
repo first" hint (paired with the #384 push-workflow button) rather
than a generic API error.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.parse
import urllib.request

from astrolift_registry.models import RegisteredApp
from astrolift_scm.ci_templates import default_workflow_path_for
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers.github import GITHUB_API_DEFAULT, GithubProviderError, _token

# ---------------------------------------------------------------------------
# Result + error codes
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class WorkflowDispatchResult:
    """Outcome of a workflow-dispatch call.

    ``ok=True``: the host accepted the dispatch. GitHub's
    workflow-dispatch endpoint returns 204 with no body, so
    ``run_url`` is a best-effort link to the workflow's *runs page*
    rather than a specific run id — the run id isn't observable
    until the workflow starts. The frontend opens this URL in a new
    tab; the operator sees the new run materialize at the top.

    ``ok=False``: ``error`` carries a stable code + message. Codes:
      - ``WORKFLOW_FILE_MISSING``: the workflow path returned 404 on
        the host. UI should prompt the operator to push the workflow
        file (#384) before retrying.
      - ``AUTH_FAILED``: the host rejected the token.
      - ``NOT_FOUND``: the repo or branch wasn't found.
      - ``API_ERROR``: any other non-2xx from the host.
      - ``NETWORK``: couldn't reach the host.
    """

    ok: bool
    run_url: str = ""
    error_code: str = ""
    error_message: str = ""


class WorkflowDispatchError(Exception):
    """Raised when the caller's invariants don't hold (no connection,
    unsupported host, etc.). Network / API failures are returned as
    ``WorkflowDispatchResult(ok=False, ...)`` instead — only true
    "should never happen at the call site" conditions raise."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Connection picker (mirrors astrolift_registry.services.manifest_sync)
# ---------------------------------------------------------------------------


# App-install first (no silent token expiry, scoped per-installation),
# then OAuth-user, then PAT. Matches the resync service's policy so
# the same connection drives manifest reads + workflow dispatches.
_KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "github": (
        "github_app_install",
        "github_oauth_user",
        "github_pat",
    ),
}


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
    """Pick the highest-ranked active, non-orphaned connection in the
    app's org whose kind matches the app's source host. Returns None
    when no usable row exists — caller surfaces this as a
    PRECONDITION at the resolver layer."""
    accepted = _KIND_PREFERENCE.get(app.source_kind, ())
    if not accepted:
        return None

    rows = list(
        SourceConnection.objects.filter(
            organization_id=app.organization_id,
            kind__in=accepted,
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
    )
    if not rows:
        return None
    rank = {k: i for i, k in enumerate(accepted)}
    rows.sort(key=lambda r: (rank.get(r.kind, len(accepted)), r.pk))
    return rows[0]


# ---------------------------------------------------------------------------
# GitHub workflow-dispatch
# ---------------------------------------------------------------------------


def _github_api_base(connection: SourceConnection) -> str:
    return (connection.api_base_url or GITHUB_API_DEFAULT).rstrip("/")


def _github_auth_header(connection: SourceConnection, token: str) -> str:
    return f"Bearer {token}" if connection.kind == "github_app_install" else f"token {token}"


def _github_runs_url(repo_full_name: str, workflow_path: str) -> str:
    """The human-facing URL for a workflow file's run history. GitHub's
    workflow-dispatch endpoint returns 204 with no body, so we
    synthesize the runs-page URL from the workflow's filename — the
    operator opens it and watches the new run appear at the top."""
    file_name = workflow_path.rsplit("/", 1)[-1]
    return f"https://github.com/{repo_full_name}/actions/workflows/" f"{urllib.parse.quote(file_name)}"


def _dispatch_github_workflow(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    workflow_path: str,
    branch: str,
) -> WorkflowDispatchResult:
    """POST to GitHub's workflow-dispatch endpoint.

    URL shape: ``/repos/{owner}/{repo}/actions/workflows/{file}/dispatches``
    where ``{file}`` is the workflow filename (e.g. ``astrolift-ci.yml``);
    GitHub accepts either the numeric workflow ID or the file basename.
    The basename is more stable across repo migrations + reads better
    in audit logs.
    """
    try:
        token = _token(connection)
    except GithubProviderError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

    base = _github_api_base(connection)
    file_name = workflow_path.rsplit("/", 1)[-1]
    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))
    safe_file = urllib.parse.quote(file_name, safe="")
    url = f"{base}/repos/{safe_repo}/actions/workflows/{safe_file}/dispatches"

    body = {"ref": branch}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            # 204 No Content on success; nothing to parse.
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        # GitHub returns 404 in two distinct cases at this endpoint:
        # (a) the workflow file doesn't exist on the default branch,
        # and (b) the repo or branch doesn't exist. The response body
        # for (a) contains "workflow", which lets us distinguish.
        if exc.code == 404:
            if "workflow" in body_text.lower():
                return WorkflowDispatchResult(
                    ok=False,
                    error_code="WORKFLOW_FILE_MISSING",
                    error_message=(
                        f"GitHub couldn't find {workflow_path!r} on the default branch. "
                        "Click 'Sync workflow file' on the app's CI setup section "
                        "(or run pushAstroliftCiWorkflowToRepo), then retry."
                    ),
                )
            return WorkflowDispatchResult(
                ok=False,
                error_code="NOT_FOUND",
                error_message=(
                    f"GitHub couldn't find {repo_full_name}@{branch}. " "Check the connection's repo access."
                ),
            )
        if exc.code in (401, 403):
            return WorkflowDispatchResult(
                ok=False,
                error_code="AUTH_FAILED",
                error_message=f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
            )
        if exc.code == 422:
            # Common 422: "Workflow does not have 'workflow_dispatch'
            # trigger." Treat as a config issue so the FE can guide
            # the operator to add the trigger stanza.
            return WorkflowDispatchResult(
                ok=False,
                error_code="WORKFLOW_NOT_DISPATCHABLE",
                error_message=(
                    f"GitHub refused dispatch ({exc.code}): {body_text or 'workflow may be missing a workflow_dispatch trigger'}"
                ),
            )
        return WorkflowDispatchResult(
            ok=False,
            error_code="API_ERROR",
            error_message=f"GitHub returned {exc.code}: {body_text}",
        )
    except urllib.error.URLError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code="NETWORK",
            error_message=f"Couldn't reach GitHub: {exc.reason}",
        )

    return WorkflowDispatchResult(
        ok=True,
        run_url=_github_runs_url(repo_full_name, workflow_path),
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def dispatch_astrolift_ci_workflow(
    app: RegisteredApp,
    *,
    branch: str | None = None,
    workflow_path: str | None = None,
) -> WorkflowDispatchResult:
    """Trigger the configured CI workflow for ``app`` on its source host.

    ``branch`` defaults to ``app.deploy_branch`` (falling back to
    ``main``); ``workflow_path`` defaults to the convention for the
    host (``.github/workflows/astrolift-ci.yml`` for GitHub).

    Picks the highest-ranked active SourceConnection in the app's
    org. Raises ``WorkflowDispatchError`` for caller-fault conditions
    (no connection, host without dispatch support); returns a
    ``WorkflowDispatchResult(ok=False, ...)`` for runtime failures
    against the host so the GraphQL resolver can map them into the
    MutationResult envelope.
    """
    if not app.source_repo:
        raise WorkflowDispatchError(
            "NO_SOURCE_REPO",
            "app has no source repo configured; cannot dispatch a workflow",
        )

    if app.source_kind in {"gitlab", "bitbucket", "gitea", "git_url"}:
        raise NotImplementedError(
            "Workflow dispatch is GitHub-only for now; " "use a manual pipeline trigger on GitLab."
        )
    if app.source_kind != "github":
        raise WorkflowDispatchError(
            "UNSUPPORTED_SOURCE",
            f"unsupported source_kind {app.source_kind!r} for workflow dispatch",
        )

    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowDispatchError(
            "NO_CONNECTION",
            ("no active source connection for this app's org. " "Connect a GitHub identity, then retry."),
        )

    resolved_branch = (branch or app.deploy_branch or "main").strip()
    if not resolved_branch:
        resolved_branch = "main"

    resolved_path = (workflow_path or default_workflow_path_for(app.source_kind)).lstrip("/")
    # Spec calls out ``.github/workflows/astrolift-ci.yml`` as the
    # convention here, distinct from the deploy-workflow path used by
    # #384's pushCiWorkflow. The default helper returns the deploy
    # path; substitute the CI path unless the caller passed an
    # explicit override.
    if app.source_kind == "github" and workflow_path is None:
        resolved_path = ".github/workflows/astrolift-ci.yml"

    return _dispatch_github_workflow(
        connection,
        repo_full_name=app.source_repo,
        workflow_path=resolved_path,
        branch=resolved_branch,
    )
