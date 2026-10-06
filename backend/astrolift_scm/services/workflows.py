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
from astrolift_scm.providers.bitbucket import (
    BitbucketProviderError,
)
from astrolift_scm.providers.bitbucket import (
    _auth_header as _bb_auth_header,
)
from astrolift_scm.providers.bitbucket import (
    _split_workspace_repo as _bb_split,
)
from astrolift_scm.providers.gitea import (
    GiteaProviderError,
)
from astrolift_scm.providers.gitea import (
    _api_base as _gitea_api_base,
)
from astrolift_scm.providers.gitea import (
    _token as _gitea_token,
)
from astrolift_scm.providers.github import GITHUB_API_DEFAULT, GithubProviderError, _token
from astrolift_scm.providers.gitlab import (
    GitlabProviderError,
)
from astrolift_scm.providers.gitlab import (
    _api_base as _gitlab_api_base,
)
from astrolift_scm.providers.gitlab import (
    _token as _gitlab_token,
)

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
# Connection picker
# ---------------------------------------------------------------------------


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
    """Resolve the org-level connection for ``app``'s source host.

    Dispatching a workflow is an autonomous platform action that runs
    under an org identity, but it is NOT a secrets write, so it uses
    ``purpose=ORG_REPO_WRITE``: the GitHub App is preferred, then an
    org-level OAuth-user, then a PAT (an App-less org that onboarded via
    OAuth/PAT still dispatches). The selection is org-scoped, never the
    requesting viewer's personal token. Returns None (not raising) so the
    caller surfaces the miss as a PRECONDITION at the resolver layer."""
    from astrolift_scm.services.connection_resolver import (
        ORG_REPO_WRITE,
        ConnectionResolutionError,
        resolve_connection,
    )

    try:
        return resolve_connection(
            app.organization_id,
            purpose=ORG_REPO_WRITE,
            source_kind=app.source_kind,
            repo=app.source_repo,
        )
    except ConnectionResolutionError:
        return None


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
# GitLab pipeline trigger (#532)
# ---------------------------------------------------------------------------


def _gitlab_runs_url(connection: SourceConnection, repo_full_name: str) -> str:
    base = _gitlab_api_base(connection).rstrip("/")
    # Strip the /api prefix to get the web base (self-hosted GitLab keeps the
    # same host; api_base_url stores the API root, e.g. https://gl.example.com).
    web_base = base.split("/api")[0].rstrip("/")
    return f"{web_base}/{repo_full_name}/-/pipelines"


def _dispatch_gitlab_pipeline(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> WorkflowDispatchResult:
    """POST /api/v4/projects/{encoded_path}/pipeline.

    Creates a new pipeline on ``branch`` using the SourceConnection
    Bearer/PAT token. Returns the pipeline's web URL on success.
    GitLab returns 400 when the ``.gitlab-ci.yml`` file is absent or
    invalid; that maps to ``WORKFLOW_FILE_MISSING`` so the UI can
    surface the sync hint.
    """
    try:
        token = _gitlab_token(connection)
    except GitlabProviderError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

    base = _gitlab_api_base(connection).rstrip("/")
    encoded_path = urllib.parse.quote(repo_full_name, safe="")
    url = f"{base}/api/v4/projects/{encoded_path}/pipeline"
    body = json.dumps({"ref": branch}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            return WorkflowDispatchResult(
                ok=False,
                error_code="AUTH_FAILED",
                error_message=f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
            )
        if exc.code == 404:
            return WorkflowDispatchResult(
                ok=False,
                error_code="NOT_FOUND",
                error_message=f"GitLab couldn't find {repo_full_name!r}. Check the connection's repo access.",
            )
        if exc.code == 400:
            body_lower = body_text.lower()
            if any(kw in body_lower for kw in ("config", "yaml", "ci file", "gitlab-ci")):
                return WorkflowDispatchResult(
                    ok=False,
                    error_code="WORKFLOW_FILE_MISSING",
                    error_message=(
                        f"GitLab refused the pipeline ({exc.code}): {body_text or 'missing or invalid .gitlab-ci.yml'}. "
                        "Click 'Sync workflow file' on the app's CI setup section, then retry."
                    ),
                )
            return WorkflowDispatchResult(
                ok=False,
                error_code="API_ERROR",
                error_message=f"GitLab returned {exc.code}: {body_text}",
            )
        return WorkflowDispatchResult(
            ok=False,
            error_code="API_ERROR",
            error_message=f"GitLab returned {exc.code}: {body_text}",
        )
    except urllib.error.URLError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code="NETWORK",
            error_message=f"Couldn't reach GitLab: {exc.reason}",
        )

    web_url = (data or {}).get("web_url") or _gitlab_runs_url(connection, repo_full_name)
    return WorkflowDispatchResult(ok=True, run_url=web_url)


# ---------------------------------------------------------------------------
# Bitbucket pipeline trigger (#532)
# ---------------------------------------------------------------------------

_BITBUCKET_API_BASE = "https://api.bitbucket.org"
_BITBUCKET_WEB_BASE = "https://bitbucket.org"


def _dispatch_bitbucket_pipeline(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> WorkflowDispatchResult:
    """POST /2.0/repositories/{workspace}/{slug}/pipelines/.

    Triggers a Bitbucket Pipelines run against ``branch``. Auth via the
    SourceConnection credential (Bearer for oauth_user; Basic for pat).
    Returns the pipeline's Bitbucket web URL.
    """
    try:
        auth_header = _bb_auth_header(connection)
        workspace, slug = _bb_split(repo_full_name)
    except BitbucketProviderError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

    url = f"{_BITBUCKET_API_BASE}/2.0/repositories/{workspace}/{slug}/pipelines/"
    body = json.dumps(
        {
            "target": {
                "ref_type": "branch",
                "type": "pipeline_ref_target",
                "ref_name": branch,
            }
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            return WorkflowDispatchResult(
                ok=False,
                error_code="AUTH_FAILED",
                error_message=f"Bitbucket rejected the token ({exc.code}). Reconnect or rotate.",
            )
        if exc.code == 404:
            body_lower = body_text.lower()
            if any(kw in body_lower for kw in ("pipeline", "config", "bitbucket-pipelines")):
                return WorkflowDispatchResult(
                    ok=False,
                    error_code="WORKFLOW_FILE_MISSING",
                    error_message=(
                        "Bitbucket Pipelines is not enabled or bitbucket-pipelines.yml is missing. "
                        "Click 'Sync workflow file' on the app's CI setup section, then retry."
                    ),
                )
            return WorkflowDispatchResult(
                ok=False,
                error_code="NOT_FOUND",
                error_message=f"Bitbucket couldn't find {repo_full_name!r}. Check the connection's repo access.",
            )
        return WorkflowDispatchResult(
            ok=False,
            error_code="API_ERROR",
            error_message=f"Bitbucket returned {exc.code}: {body_text}",
        )
    except urllib.error.URLError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code="NETWORK",
            error_message=f"Couldn't reach Bitbucket: {exc.reason}",
        )

    build_number = (data or {}).get("build_number")
    if build_number:
        run_url = f"{_BITBUCKET_WEB_BASE}/{workspace}/{slug}/pipelines/{build_number}"
    else:
        run_url = f"{_BITBUCKET_WEB_BASE}/{workspace}/{slug}/pipelines"
    return WorkflowDispatchResult(ok=True, run_url=run_url)


# ---------------------------------------------------------------------------
# Gitea workflow dispatch (#532)
# ---------------------------------------------------------------------------


def _dispatch_gitea_workflow(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    workflow_path: str,
    branch: str,
) -> WorkflowDispatchResult:
    """POST /api/v1/repos/{owner}/{repo}/actions/workflows/{filename}/dispatches.

    Gitea Actions uses GitHub Actions syntax. The endpoint returns 204
    on success. Falls back gracefully when Gitea Actions is disabled
    on the instance (404 → WORKFLOW_FILE_MISSING).
    """
    try:
        token = _gitea_token(connection)
        base = _gitea_api_base(connection).rstrip("/")
    except GiteaProviderError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

    owner, repo = repo_full_name.split("/", 1)
    file_name = workflow_path.rsplit("/", 1)[-1]
    safe_owner = urllib.parse.quote(owner, safe="")
    safe_repo = urllib.parse.quote(repo, safe="")
    safe_file = urllib.parse.quote(file_name, safe="")
    url = f"{base}/api/v1/repos/{safe_owner}/{safe_repo}/actions/workflows/{safe_file}/dispatches"

    body = json.dumps({"ref": branch}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            return WorkflowDispatchResult(
                ok=False,
                error_code="AUTH_FAILED",
                error_message=f"Gitea rejected the token ({exc.code}). Reconnect or rotate.",
            )
        if exc.code == 404:
            return WorkflowDispatchResult(
                ok=False,
                error_code="WORKFLOW_FILE_MISSING",
                error_message=(
                    f"Gitea couldn't find {workflow_path!r} on {repo_full_name!r}. "
                    "Actions may be disabled on this Gitea instance, or the workflow file is missing. "
                    "Click 'Sync workflow file' on the app's CI setup section, then retry."
                ),
            )
        return WorkflowDispatchResult(
            ok=False,
            error_code="API_ERROR",
            error_message=f"Gitea returned {exc.code}: {body_text}",
        )
    except urllib.error.URLError as exc:
        return WorkflowDispatchResult(
            ok=False,
            error_code="NETWORK",
            error_message=f"Couldn't reach Gitea: {exc.reason}",
        )

    run_url = f"{base}/{owner}/{repo}/actions"
    return WorkflowDispatchResult(ok=True, run_url=run_url)


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

    supported = {"github", "gitlab", "bitbucket", "gitea"}
    if app.source_kind not in supported:
        raise WorkflowDispatchError(
            "UNSUPPORTED_SOURCE",
            f"unsupported source_kind {app.source_kind!r} for workflow dispatch; "
            f"supported: {', '.join(sorted(supported))}",
        )

    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowDispatchError(
            "NO_CONNECTION",
            f"no active source connection for this app's org. "
            f"Connect a {app.source_kind.title()} identity, then retry.",
        )

    resolved_branch = (branch or app.deploy_branch or "main").strip() or "main"

    if app.source_kind == "github":
        from astrolift_scm.services.workflow_sync import github_workflow_path_for

        resolved_path = (workflow_path or github_workflow_path_for(app)).lstrip("/")
        return _dispatch_github_workflow(
            connection,
            repo_full_name=app.source_repo,
            workflow_path=resolved_path,
            branch=resolved_branch,
        )

    if app.source_kind == "gitlab":
        return _dispatch_gitlab_pipeline(
            connection,
            repo_full_name=app.source_repo,
            branch=resolved_branch,
        )

    if app.source_kind == "bitbucket":
        return _dispatch_bitbucket_pipeline(
            connection,
            repo_full_name=app.source_repo,
            branch=resolved_branch,
        )

    # gitea
    resolved_path = (workflow_path or default_workflow_path_for("gitea")).lstrip("/")
    return _dispatch_gitea_workflow(
        connection,
        repo_full_name=app.source_repo,
        workflow_path=resolved_path,
        branch=resolved_branch,
    )
