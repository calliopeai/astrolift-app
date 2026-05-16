"""Push the Astrolift CI workflow file to a tenant's source repo (#384).

Companion to ``astrolift_scm.services.workflows`` (#387): where that
module's :func:`dispatch_astrolift_ci_workflow` *triggers* the workflow,
this one *materializes the YAML* into ``.github/workflows/astrolift-ci.yml``
on the configured deploy branch so the dispatch endpoint actually has
a workflow file to run against. The pairing exists because a fresh app
won't have the workflow yet on the operator's side, and the inline
"paste this YAML" affordance on the Settings page is friction we'd
rather skip when we already hold a SourceConnection that can write.

Render → fetch existing → compare → write-or-skip. The render is a
small string-substitution pass against
``astrolift_lifecycle/templates/astrolift-ci.yml.j2``; full Jinja2 isn't
in the runtime image, and the substitution surface here is five fixed
variables with no logic, so we keep the template loadable as a
``.j2``-suffixed asset (so an operator's editor highlights it sensibly)
and do the substitution by hand. If template logic ever grows past
``{{ var }}`` we'll graduate the package to a real Jinja env.

Branch-protection handling: a "compliant" GitHub repo will gate direct
pushes to the deploy branch. We probe the protections endpoint before
PUT-ing the file; when the branch is protected, we land the file on
a side branch and open a PR instead so the change goes through the
repo's review path. The mutation surface returns
``status="pr_opened"`` with the PR URL for that case.
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Final

from django.conf import settings

from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError, fetch_file, put_file
from astrolift_scm.providers.github import GITHUB_API_DEFAULT, GithubProviderError, _token

# Path the operator's deploy webhook expects on disk. Matches the
# convention referenced from #387's dispatch service so the dispatch
# endpoint and the sync endpoint key off the same filename.
WORKFLOW_PATH: Final = ".github/workflows/astrolift-ci.yml"

# Connection-kind preference order — same shape as #387 so a single
# active connection drives both the dispatch and the push.
_KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "github": (
        "github_app_install",
        "github_oauth_user",
        "github_pat",
    ),
}


@dataclasses.dataclass(frozen=True, slots=True)
class WorkflowSyncResult:
    """Outcome of :func:`sync_workflow_file_to_repo`.

    ``status`` is the discriminator the resolver surfaces verbatim:

    * ``created``      — file was missing on the deploy branch; we
                          committed the rendered content.
    * ``updated``      — file existed but its body diverged from the
                          rendered template; we overwrote it.
    * ``in_sync``      — file existed and matched byte-for-byte; we
                          did nothing.
    * ``pr_opened``    — deploy branch is protected; we landed the
                          content on a side branch and opened a PR.
                          ``pr_url`` is the host-side review link.
    * ``fetch_failed`` — couldn't reach the host or the connection
                          token was rejected. ``error`` carries the
                          host-side message.
    """

    status: str
    commit_sha: str = ""
    pr_url: str = ""
    rendered_size: int = 0
    error: str = ""


class WorkflowSyncError(Exception):
    """Raised for caller-fault conditions (no source repo, unsupported
    host, no usable connection). Host-side failures are returned as a
    ``WorkflowSyncResult`` with ``status="fetch_failed"`` so the
    resolver can map them to a clean ``MutationResult`` envelope."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "astrolift_lifecycle" / "templates" / "astrolift-ci.yml.j2"
)

# Matches ``{{ name }}`` — single substitution variable with optional
# whitespace. Deliberately conservative: any structure beyond a bare
# identifier is left in place so a stray ``${{ github.sha }}`` inside
# the template body (a literal GitHub Actions expression, not a Jinja
# variable) is untouched.
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def render_astrolift_ci_workflow(app: RegisteredApp) -> str:
    """Render the workflow YAML for ``app`` against the file template.

    The five variables are pulled from the app's persisted state:

    * ``app_slug``      — ``RegisteredApp.slug``
    * ``deploy_branch`` — ``app.deploy_branch`` (fallback: ``main``)
    * ``ecr_uri``       — ``app.registry_repo_uri``
    * ``push_role_arn`` — ``app.push_role_ref``
    * ``api_url``       — ``settings.PLATFORM_API_URL`` (trimmed)

    The deploy token is NOT templated — the workflow references it
    through ``${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}`` so plaintext
    never sits in a file in the operator's repo. Same goes for the
    role ARN's session naming — the template uses
    ``${{ github.run_id }}`` directly.
    """
    template = _load_template()
    api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    values = {
        "app_slug": app.slug,
        "deploy_branch": (app.deploy_branch or "main").strip() or "main",
        "ecr_uri": app.registry_repo_uri or "",
        "push_role_arn": app.push_role_ref or "",
        "api_url": api_url,
    }

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            # Leave unknown identifiers in place — defensive against
            # a sibling agent extending the template with a variable
            # before this code learns about it.
            return match.group(0)
        return values[name]

    return _VAR_RE.sub(_replace, template)


# ---------------------------------------------------------------------------
# Source-connection picker
# ---------------------------------------------------------------------------


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
    """Same selection as :func:`astrolift_scm.services.workflows._pick_source_connection`.

    Documented choice: org-wide preference order (app-install >
    oauth-user > pat). We deliberately don't filter to the viewer's
    personal connection because writing a workflow file is a
    platform-side bookkeeping action — any org-active GitHub identity
    can land it, and gating on the viewer's personal connection would
    block operators who connected the repo via a teammate's PAT.
    """
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
# GitHub branch-protection probe + PR fallback
# ---------------------------------------------------------------------------


def _github_api_base(connection: SourceConnection) -> str:
    return (connection.api_base_url or GITHUB_API_DEFAULT).rstrip("/")


def _github_auth_header(connection: SourceConnection, token: str) -> str:
    return f"Bearer {token}" if connection.kind == "github_app_install" else f"token {token}"


def _safe_repo(repo_full_name: str) -> str:
    return "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))


def _is_github_branch_protected(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> bool:
    """GET /repos/{owner}/{repo}/branches/{branch}/protection.

    404 → unprotected; 200 → protected; auth / other errors bubble as
    "treat as unprotected" so we don't unnecessarily push every change
    through a PR. The mutation surface still surfaces a downstream
    write failure cleanly via ``fetch_failed``.
    """
    token = _token(connection)
    base = _github_api_base(connection)
    safe_branch = urllib.parse.quote(branch, safe="")
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/branches/{safe_branch}/protection"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        # 401/403/etc. — fall through to "not protected" so the write
        # path attempts a direct PUT. If the write also auth-fails,
        # the operator sees that error instead of an opaque "PR
        # opened" detour.
        return False
    except urllib.error.URLError:
        return False


def _github_get_branch_sha(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> str:
    """Return the head commit SHA of ``branch``. Raises
    ``GithubProviderError`` on any failure — caller catches and
    surfaces as ``fetch_failed``."""
    token = _token(connection)
    base = _github_api_base(connection)
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/branches/{urllib.parse.quote(branch, safe='')}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GithubProviderError(
            "API_ERROR",
            f"couldn't resolve {branch!r} on {repo_full_name}: {exc.code}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"couldn't reach GitHub: {exc.reason}") from exc
    sha = ((payload or {}).get("commit") or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise GithubProviderError("UNEXPECTED_SHAPE", "GitHub /branches response missing commit.sha")
    return sha


def _github_create_ref(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    new_branch: str,
    from_sha: str,
) -> None:
    """POST /repos/{owner}/{repo}/git/refs. Idempotent on 422
    ("Reference already exists") — the same side-branch name from a
    previous attempt is fine to reuse."""
    token = _token(connection)
    base = _github_api_base(connection)
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/git/refs"
    body = {"ref": f"refs/heads/{new_branch}", "sha": from_sha}
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if exc.code == 422 and "already exists" in body_text.lower():
            return
        raise GithubProviderError(
            "API_ERROR",
            f"couldn't create ref {new_branch!r}: {exc.code} {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"couldn't reach GitHub: {exc.reason}") from exc


def _github_open_pull_request(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    head: str,
    base_branch: str,
    title: str,
    body: str,
) -> str:
    """POST /repos/{owner}/{repo}/pulls. Returns the PR's web URL.

    422 with "A pull request already exists" is treated as success —
    we look up the open PR for ``head:base`` and return its URL."""
    token = _token(connection)
    api_base = _github_api_base(connection)
    url = f"{api_base}/repos/{_safe_repo(repo_full_name)}/pulls"
    payload = {"title": title, "head": head, "base": base_branch, "body": body}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code == 422 and "already exists" in body_text.lower():
            return _github_find_existing_pr(
                connection,
                repo_full_name=repo_full_name,
                head=head,
                base_branch=base_branch,
            )
        raise GithubProviderError(
            "API_ERROR",
            f"couldn't open PR for {head!r}: {exc.code} {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"couldn't reach GitHub: {exc.reason}") from exc
    web_url = (data or {}).get("html_url") or ""
    if not isinstance(web_url, str) or not web_url:
        raise GithubProviderError("UNEXPECTED_SHAPE", "GitHub PR-create response missing html_url")
    return web_url


def _github_find_existing_pr(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    head: str,
    base_branch: str,
) -> str:
    """GET /repos/{owner}/{repo}/pulls?head=owner:branch&base=...
    Returns the first open PR's html_url, or empty string if none."""
    token = _token(connection)
    api_base = _github_api_base(connection)
    owner = repo_full_name.split("/", 1)[0]
    head_param = f"{owner}:{head}"
    url = (
        f"{api_base}/repos/{_safe_repo(repo_full_name)}/pulls"
        f"?state=open&head={urllib.parse.quote(head_param, safe=':')}"
        f"&base={urllib.parse.quote(base_branch, safe='')}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return ""
    except urllib.error.URLError:
        return ""
    if isinstance(rows, list) and rows:
        first = rows[0] or {}
        return first.get("html_url") or ""
    return ""


# ---------------------------------------------------------------------------
# Side-branch name
# ---------------------------------------------------------------------------


def _side_branch_for(app_slug: str) -> str:
    """Deterministic side branch when the deploy branch is protected.

    Same slug on every run → reopening the workflow PR after a no-op
    edit on the same operator reuses the existing PR instead of
    fanning out review noise."""
    return f"astrolift/ci-workflow-{app_slug}"


# ---------------------------------------------------------------------------
# Top-level sync
# ---------------------------------------------------------------------------


def sync_workflow_file_to_repo(
    app: RegisteredApp,
    viewer_user=None,  # noqa: ARG001 — accepted for API symmetry; org-level conn picker is used
) -> WorkflowSyncResult:
    """Render the CI workflow for ``app`` and reconcile it onto the
    deploy branch of the configured source repo.

    See module docstring for the protocol. ``viewer_user`` is accepted
    for parity with the resolver signature but is not consulted —
    selection runs against the app's organization, mirroring #387.
    """
    if not app.source_repo:
        raise WorkflowSyncError(
            "NO_SOURCE_REPO",
            "app has no source repo configured; cannot push the CI workflow",
        )
    if app.source_kind in {"gitlab", "bitbucket", "gitea", "git_url"}:
        raise NotImplementedError(
            "Pushing the Astrolift CI workflow is GitHub-only for now; "
            "see the inline reference YAML in Settings → CI setup for non-GitHub hosts."
        )
    if app.source_kind != "github":
        raise WorkflowSyncError(
            "UNSUPPORTED_SOURCE",
            f"unsupported source_kind {app.source_kind!r} for workflow push",
        )

    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowSyncError(
            "NO_CONNECTION",
            "no active source connection for this app's org. "
            "Connect a GitHub identity under Settings → Source connections, then retry.",
        )

    rendered = render_astrolift_ci_workflow(app)
    rendered_size = len(rendered.encode("utf-8"))
    deploy_branch = (app.deploy_branch or "main").strip() or "main"

    # 1. Read existing file. None → create path; equal → no-op.
    try:
        existing = fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=WORKFLOW_PATH,
            ref=deploy_branch,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    if existing is not None and existing == rendered:
        return WorkflowSyncResult(
            status="in_sync",
            rendered_size=rendered_size,
        )

    # 2. Protection probe — only on the update / create path, where
    # we're actually about to write. Read-only branches will land here
    # rather than auth-error on the write.
    protected = _is_github_branch_protected(
        connection,
        repo_full_name=app.source_repo,
        branch=deploy_branch,
    )

    commit_message = (
        f"chore(astrolift): sync CI workflow for {app.slug}"
        if existing is None
        else f"chore(astrolift): update CI workflow for {app.slug}"
    )

    if protected:
        # PR path: branch from the deploy branch's head, write the
        # file there, open a PR back into the deploy branch.
        side_branch = _side_branch_for(app.slug)
        try:
            head_sha = _github_get_branch_sha(
                connection,
                repo_full_name=app.source_repo,
                branch=deploy_branch,
            )
            _github_create_ref(
                connection,
                repo_full_name=app.source_repo,
                new_branch=side_branch,
                from_sha=head_sha,
            )
            put_file(
                connection,
                repo_full_name=app.source_repo,
                path=WORKFLOW_PATH,
                branch=side_branch,
                content=rendered,
                commit_message=commit_message,
            )
            pr_url = _github_open_pull_request(
                connection,
                repo_full_name=app.source_repo,
                head=side_branch,
                base_branch=deploy_branch,
                title=f"Astrolift: sync CI workflow for {app.slug}",
                body=(
                    "This PR was opened by Astrolift to keep "
                    f"`{WORKFLOW_PATH}` in sync with the platform's "
                    f"current settings for **{app.slug}**.\n\n"
                    "Merge to enable platform-driven deploys against "
                    f"`{deploy_branch}`."
                ),
            )
        except GithubProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        except ProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        return WorkflowSyncResult(
            status="pr_opened",
            pr_url=pr_url,
            rendered_size=rendered_size,
        )

    # 3. Direct write path.
    try:
        put_result = put_file(
            connection,
            repo_full_name=app.source_repo,
            path=WORKFLOW_PATH,
            branch=deploy_branch,
            content=rendered,
            commit_message=commit_message,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    return WorkflowSyncResult(
        status="created" if existing is None else "updated",
        commit_sha=put_result.commit_sha,
        rendered_size=rendered_size,
    )
