"""Push the Astrolift CI workflow file to a tenant's source repo (#384).

Companion to ``astrolift_scm.services.workflows`` (#387): where that
module's :func:`dispatch_astrolift_ci_workflow` *triggers* the workflow,
this one *materializes the YAML* into the conventional CI file path on
the configured deploy branch so the dispatch endpoint actually has a
workflow file to run against. The pairing exists because a fresh app
won't have the workflow yet on the operator's side, and the inline
"paste this YAML" affordance on the Settings page is friction we'd
rather skip when we already hold a SourceConnection that can write.

Render → fetch existing → compare → write-or-skip. The GitHub renderer
does a small string-substitution pass against
``astrolift_lifecycle/templates/astrolift-ci.yml.j2``; the GitLab
renderer (#735) builds the equivalent document inline using the same
five variables. Branch-protection handling: a "compliant" repo will
gate direct pushes to the deploy branch. We probe the protections
endpoint before PUT-ing; when the branch is protected, we land the
file on a side branch and open a PR/MR so the change goes through the
repo's review path. ``status="pr_opened"`` is returned in that case.
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
from astrolift_scm.providers import ProviderError, fetch_file, open_pull_request, put_file
from astrolift_scm.providers.github import GITHUB_API_DEFAULT, GithubProviderError, _token
from astrolift_scm.providers.gitlab import (
    GitlabProviderError,
    _api_base as _gitlab_api_base,
    _token as _gitlab_token,
)

# Conventional CI workflow paths per host.
WORKFLOW_PATH: Final = ".github/workflows/astrolift-ci.yml"
GITLAB_WORKFLOW_PATH: Final = ".gitlab-ci.yml"

# Connection-kind preference order — same shape as #387 so a single
# active connection drives both the dispatch and the push.
_KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "github": (
        "github_app_install",
        "github_oauth_user",
        "github_pat",
    ),
    "gitlab": (
        "gitlab_oauth_user",
        "gitlab_pat",
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


def render_astrolift_gitlab_ci_workflow(app: RegisteredApp) -> str:
    """Render the GitLab CI equivalent of the GitHub Actions template (#735).

    Uses docker:24-dind to build and push to ECR. Static AWS credentials
    are expected as ``ASTROLIFT_AWS_ACCESS_KEY_ID``,
    ``ASTROLIFT_AWS_SECRET_ACCESS_KEY``, and ``ASTROLIFT_AWS_DEFAULT_REGION``
    GitLab CI variables (set by the variables-push mutation, #531). OIDC is
    not assumed here because GitLab runner OIDC with AWS requires additional
    runner-level config the operator may not have.
    """
    api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    ecr_uri = app.registry_repo_uri or ""
    # ECR login endpoint is just the registry host, not the repo path.
    ecr_registry = ecr_uri.split("/")[0] if ecr_uri else ""
    deploy_branch = (app.deploy_branch or "main").strip() or "main"
    slug_literal = json.dumps(app.slug)
    api_url_literal = json.dumps(api_url)
    rules_line = f"    - if: '$CI_COMMIT_REF_NAME == \"{deploy_branch}\"'"
    return (
        "# Managed by Astrolift — do not edit by hand."
        " Re-sync via Settings → CI setup → Sync workflow file.\n"
        "stages:\n"
        "  - build\n"
        "  - deploy\n"
        "\n"
        "variables:\n"
        f"  ASTROLIFT_IMAGE: \"{ecr_uri}:$CI_COMMIT_SHA\"\n"
        "\n"
        "build-image:\n"
        "  stage: build\n"
        "  image: docker:24-dind\n"
        "  services:\n"
        "    - docker:24-dind\n"
        "  variables:\n"
        "    DOCKER_TLS_CERTDIR: /certs\n"
        "    AWS_ACCESS_KEY_ID: $ASTROLIFT_AWS_ACCESS_KEY_ID\n"
        "    AWS_SECRET_ACCESS_KEY: $ASTROLIFT_AWS_SECRET_ACCESS_KEY\n"
        "    AWS_DEFAULT_REGION: $ASTROLIFT_AWS_DEFAULT_REGION\n"
        "  rules:\n"
        f"{rules_line}\n"
        "  script:\n"
        "    - apk add --no-cache aws-cli\n"
        f"    - aws ecr get-login-password | docker login --username AWS --password-stdin \"{ecr_registry}\"\n"
        "    - docker build -t \"$ASTROLIFT_IMAGE\" .\n"
        "    - docker push \"$ASTROLIFT_IMAGE\"\n"
        "\n"
        "notify-astrolift:\n"
        "  stage: deploy\n"
        "  image: ubuntu:24.04\n"
        "  needs: [build-image]\n"
        "  variables:\n"
        f"    ASTROLIFT_API_URL: {api_url_literal}\n"
        f"    ASTROLIFT_APP_SLUG: {slug_literal}\n"
        "  rules:\n"
        f"{rules_line}\n"
        "  script:\n"
        "    - apt-get update -qq && apt-get install -y -qq curl ca-certificates\n"
        "    - |\n"
        "      curl --fail-with-body -sS -X POST \"$ASTROLIFT_API_URL/api/v1/deploys\" \\\n"
        "        -H \"Authorization: Bearer $ASTROLIFT_DEPLOY_TOKEN\" \\\n"
        "        -H \"Content-Type: application/json\" \\\n"
        "        -d \"{\\\"appSlug\\\":\\\"$ASTROLIFT_APP_SLUG\\\","
        "\\\"image\\\":\\\"$ASTROLIFT_IMAGE\\\","
        "\\\"commitSha\\\":\\\"$CI_COMMIT_SHA\\\"}\"\n"
    )


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
    """Deterministic side branch when the deploy branch is protected."""
    return f"astrolift/ci-workflow-{app_slug}"


# ---------------------------------------------------------------------------
# GitLab-specific helpers (#735)
# ---------------------------------------------------------------------------


def _is_gitlab_branch_protected(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> bool:
    """GET /projects/{id}/protected_branches/{branch}.

    200 → protected; 404 → not protected; auth/network errors → treat
    as not protected so the write path runs and surfaces any real error.
    """
    token = _gitlab_token(connection)
    base = _gitlab_api_base(connection)
    project = urllib.parse.quote(repo_full_name, safe="")
    branch_q = urllib.parse.quote(branch, safe="")
    url = f"{base}/api/v4/projects/{project}/protected_branches/{branch_q}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
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
        return False
    except urllib.error.URLError:
        return False


def _gitlab_create_branch(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    new_branch: str,
    from_branch: str,
) -> None:
    """POST /projects/{id}/repository/branches.

    Idempotent on 400 "Branch already exists".
    """
    token = _gitlab_token(connection)
    base = _gitlab_api_base(connection)
    project = urllib.parse.quote(repo_full_name, safe="")
    url = f"{base}/api/v4/projects/{project}/repository/branches"
    body = json.dumps({"branch": new_branch, "ref": from_branch}).encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        already = exc.code in (409, 422) or (
            exc.code == 400 and "already exists" in body_text.lower()
        )
        if already:
            return
        raise GitlabProviderError(
            "API_ERROR",
            f"couldn't create branch {new_branch!r}: {exc.code} {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"couldn't reach GitLab: {exc.reason}") from exc


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
    if app.source_kind == "github":
        return _sync_github(app)
    if app.source_kind == "gitlab":
        return _sync_gitlab(app)
    raise WorkflowSyncError(
        "UNSUPPORTED_SOURCE",
        f"CI workflow push is not yet supported for source_kind={app.source_kind!r}; "
        "supported hosts: github, gitlab.",
    )


def _sync_github(app: RegisteredApp) -> WorkflowSyncResult:
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
        return WorkflowSyncResult(status="in_sync", rendered_size=rendered_size)

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
        return WorkflowSyncResult(status="pr_opened", pr_url=pr_url, rendered_size=rendered_size)

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


def _sync_gitlab(app: RegisteredApp) -> WorkflowSyncResult:
    """GitLab equivalent of ``_sync_github`` (#735).

    Probes ``/protected_branches`` instead of GitHub's branch-protection
    endpoint; opens an MR (via the providers layer) instead of a PR.
    """
    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowSyncError(
            "NO_CONNECTION",
            "no active source connection for this app's org. "
            "Connect a GitLab identity under Settings → Source connections, then retry.",
        )

    rendered = render_astrolift_gitlab_ci_workflow(app)
    rendered_size = len(rendered.encode("utf-8"))
    deploy_branch = (app.deploy_branch or "main").strip() or "main"

    try:
        existing = fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=GITLAB_WORKFLOW_PATH,
            ref=deploy_branch,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    if existing is not None and existing == rendered:
        return WorkflowSyncResult(status="in_sync", rendered_size=rendered_size)

    protected = _is_gitlab_branch_protected(
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
        side_branch = _side_branch_for(app.slug)
        try:
            _gitlab_create_branch(
                connection,
                repo_full_name=app.source_repo,
                new_branch=side_branch,
                from_branch=deploy_branch,
            )
            put_file(
                connection,
                repo_full_name=app.source_repo,
                path=GITLAB_WORKFLOW_PATH,
                branch=side_branch,
                content=rendered,
                commit_message=commit_message,
            )
            mr_result = open_pull_request(
                connection,
                repo_full_name=app.source_repo,
                head_branch=side_branch,
                base_branch=deploy_branch,
                title=f"Astrolift: sync CI workflow for {app.slug}",
                body=(
                    "This MR was opened by Astrolift to keep "
                    f"`{GITLAB_WORKFLOW_PATH}` in sync with the platform's "
                    f"current settings for **{app.slug}**.\n\n"
                    "Merge to enable platform-driven deploys against "
                    f"`{deploy_branch}`."
                ),
            )
        except GitlabProviderError as exc:
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
            pr_url=mr_result.url,
            rendered_size=rendered_size,
        )

    try:
        put_result = put_file(
            connection,
            repo_full_name=app.source_repo,
            path=GITLAB_WORKFLOW_PATH,
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
