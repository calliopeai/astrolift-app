"""Tests for ``pushAstroliftCiWorkflowToRepo`` (#384, #735).

End-to-end coverage of the mutation surface: every host HTTP call
(fetch existing file, branch-protection probe, PUT contents, PR/MR
create) is stubbed at ``urllib.request.urlopen``; everything above
the wire (connection picker, template rendering, status discrimination,
permission gate, error mapping) runs against real Postgres rows.

Matrix:
  * GitHub: file-missing → ``created`` with commit_sha
  * GitHub: file Astrolift did not write → ``pr_opened``, never an
    in-place overwrite of the deploy branch
  * GitHub: file matches → ``in_sync`` (no PUT issued)
  * GitHub: protected branch → ``pr_opened`` with pr_url
  * GitLab: file-missing → ``created`` with commit_sha
  * GitLab: protected branch → ``pr_opened`` (MR) with pr_url
  * GitLab: rendered template substitutes all variables
  * no connection in org → PRECONDITION
  * unsupported host → PRECONDITION (WorkflowSyncError)
  * permission denied → PERMISSION_DENIED, no network touched
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    PushCiWorkflowToRepoInput,
)
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.workflow_sync import (
    render_astrolift_ci_workflow,
    render_astrolift_gitlab_ci_workflow,
)
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers + fixtures
# ---------------------------------------------------------------------------


def _info(user=None):
    request = SimpleNamespace(user=user)
    context = SimpleNamespace(request=request, user=user)
    return SimpleNamespace(context=context)


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _http_error(code: int, body: bytes = b"", url: str = "https://example") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


class _Response:
    """Context-manager mimicking urlopen's success shape."""

    def __init__(self, body: bytes = b"", status: int = 200):
        self._body = body
        self._status = status

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=self._status)

    def __exit__(self, *_):
        return False


@pytest.fixture
def app_with_repo(app):
    """Populate source + registry fields so the renderer has something
    to substitute and the connection picker has a host to query."""
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.default_branch = "main"
    app.deploy_branch = "main"
    app.registry_repo_uri = "111111111111.dkr.ecr.us-west-2.amazonaws.com/acme/api"
    app.push_role_ref = "arn:aws:iam::111111111111:role/astrolift-push-acme-api"
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "default_branch",
            "deploy_branch",
            "registry_repo_uri",
            "push_role_ref",
            "updated_at",
            "version",
        ]
    )
    return app


@pytest.fixture
def github_connection(org):
    encrypted = encrypt_at_rest(b"gho_test_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: org",
        account_login="org",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


@pytest.fixture
def app_with_gitlab_repo(app):
    """GitLab-sourced variant of app_with_repo."""
    app.source_kind = "gitlab"
    app.source_repo = "acme/api"
    app.default_branch = "main"
    app.deploy_branch = "main"
    app.registry_repo_uri = "111111111111.dkr.ecr.us-west-2.amazonaws.com/acme/api"
    app.push_role_ref = ""
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "default_branch",
            "deploy_branch",
            "registry_repo_uri",
            "push_role_ref",
            "updated_at",
            "version",
        ]
    )
    return app


@pytest.fixture
def gitlab_connection(org):
    encrypted = encrypt_at_rest(b"glat-test-token-never-hits-the-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_OAUTH_USER,
        display_name="GitLab: org",
        account_login="org",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


class _Router:
    """Records every urlopen call and dispatches to a per-route handler.

    Each request's behavior is driven by ``route(req) -> (kind, value)``:
        kind="response", value=bytes      → success body
        kind="status",   value=int        → success with status only
        kind="error",    value=HTTPError  → raise it
    """

    def __init__(self):
        self.calls: list[tuple[str, str]] = []  # (method, url)
        self.bodies: list[bytes | None] = []

    def install(self, monkeypatch, handler, *, host: str = "github"):
        def fake(req, timeout=15):
            self.calls.append((req.get_method(), req.full_url))
            self.bodies.append(req.data)
            kind, value = handler(req)
            if kind == "error":
                raise value
            return _Response(body=value if isinstance(value, (bytes, bytearray)) else b"")

        monkeypatch.setattr(
            "astrolift_scm.services.workflow_sync.urllib.request.urlopen",
            fake,
        )
        if host == "github":
            monkeypatch.setattr(
                "astrolift_scm.providers.github.urllib.request.urlopen",
                fake,
            )
        else:
            monkeypatch.setattr(
                "astrolift_scm.providers.gitlab.urllib.request.urlopen",
                fake,
            )


# ---------------------------------------------------------------------------
# Happy path: file missing → created
# ---------------------------------------------------------------------------


def test_creates_workflow_file_when_missing(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    permission_resolver.grant(Permission.APP_UPDATE)
    router = _Router()

    def handler(req):
        method = req.get_method()
        url = req.full_url
        if method == "GET" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            # First call: fetch_file looking for existing — 404 means missing.
            return "error", _http_error(404, b'{"message":"Not Found"}', url)
        if method == "GET" and "/branches/main/protection" in url:
            return "error", _http_error(404, b'{"message":"Branch not protected"}', url)
        if method == "PUT" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            body = json.dumps(
                {
                    "commit": {
                        "sha": "deadbeefcafe",
                        "html_url": "https://github.com/acme/api/commit/deadbeefcafe",
                    },
                    "content": {"path": ".github/workflows/astrolift-ci.yml"},
                }
            ).encode()
            return "response", body
        raise AssertionError(f"unexpected request {method} {url}")

    router.install(monkeypatch, handler)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "created"
    assert result.data.commit_sha == "deadbeefcafe"
    assert result.data.pr_url is None

    # Verify the PUT body contains the rendered template (slug + role ARN).
    put_body = next(
        (b for (m, u), b in zip(router.calls, router.bodies, strict=True) if m == "PUT" and b is not None),
        None,
    )
    assert put_body is not None
    decoded_payload = json.loads(put_body)
    import base64

    content = base64.b64decode(decoded_payload["content"]).decode("utf-8")
    assert "acme/api" in content
    assert "arn:aws:iam::111111111111:role/astrolift-push-acme-api" in content
    assert app_with_repo.slug in content
    assert decoded_payload["branch"] == "main"


# ---------------------------------------------------------------------------
# Existing file diverges → updated
# ---------------------------------------------------------------------------


def test_a_file_astrolift_did_not_write_is_not_overwritten(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """This used to PUT straight onto the deploy branch and assert "updated".

    The repo holds a file Astrolift has no baseline for -- somebody else's
    workflow, or one an operator hand-edited. Writing the template over it on
    an unprotected branch destroyed it silently. Now the change lands on a
    side branch and opens a PR, so the diff is reviewable.
    """
    permission_resolver.grant(Permission.APP_UPDATE)
    router = _Router()
    drift_body = b"# a workflow Astrolift never wrote\nname: old\n"
    put_branches: list[str] = []

    def handler(req):
        method = req.get_method()
        url = req.full_url
        accept = req.get_header("Accept") or ""
        if method == "GET" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            if accept == "application/vnd.github.raw":
                return "response", drift_body  # fetch_file path
            return "response", json.dumps({"sha": "oldblob"}).encode()
        if method == "GET" and "/branches/main/protection" in url:
            return "error", _http_error(404, b"", url)
        # Side-branch creation: read the deploy branch tip, then create the ref.
        if method == "GET" and "/branches/main" in url:
            return "response", json.dumps({"commit": {"sha": "headsha"}}).encode()
        if method == "POST" and "/git/refs" in url:
            return "response", json.dumps({"ref": "refs/heads/astrolift/ci-workflow"}).encode()
        if method == "PUT" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            body = json.loads(req.data.decode()) if req.data else {}
            put_branches.append(body.get("branch", ""))
            return "response", json.dumps(
                {
                    "commit": {
                        "sha": "newcafe1234",
                        "html_url": "https://github.com/acme/api/commit/newcafe1234",
                    },
                    "content": {"path": ".github/workflows/astrolift-ci.yml"},
                }
            ).encode()
        if method == "POST" and "/pulls" in url:
            return "response", json.dumps({"html_url": "https://github.com/acme/api/pull/9"}).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    router.install(monkeypatch, handler)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "pr_opened"
    assert put_branches, "the template still has to be written somewhere"
    assert put_branches[0] != "main", "it must not land on the deploy branch"


# ---------------------------------------------------------------------------
# Existing file matches rendered → in_sync (no PUT issued)
# ---------------------------------------------------------------------------


def test_in_sync_when_existing_matches_rendered(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    permission_resolver.grant(Permission.APP_UPDATE)
    rendered = render_astrolift_ci_workflow(app_with_repo)
    router = _Router()

    def handler(req):
        method = req.get_method()
        url = req.full_url
        accept = req.get_header("Accept") or ""
        if method == "GET" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            assert accept == "application/vnd.github.raw"
            return "response", rendered.encode("utf-8")
        raise AssertionError(f"unexpected request after match: {method} {url}")

    router.install(monkeypatch, handler)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "in_sync"
    assert result.data.commit_sha is None
    assert result.data.pr_url is None
    # Exactly one GET, no further calls.
    assert len([m for m, _ in router.calls if m == "GET"]) == 1
    assert all(m == "GET" for m, _ in router.calls)


# ---------------------------------------------------------------------------
# Protected branch → PR opened
# ---------------------------------------------------------------------------


def test_protected_branch_opens_pull_request(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    permission_resolver.grant(Permission.APP_UPDATE)
    router = _Router()

    def handler(req):
        method = req.get_method()
        url = req.full_url
        accept = req.get_header("Accept") or ""
        # Initial fetch_file: file is missing on main.
        if (
            method == "GET"
            and "/contents/.github/workflows/astrolift-ci.yml" in url
            and accept == "application/vnd.github.raw"
        ):
            return "error", _http_error(404, b"", url)
        # Protection probe: 200 → branch is protected.
        if method == "GET" and "/branches/main/protection" in url:
            return "response", json.dumps({"enabled": True}).encode()
        # Head SHA lookup for the deploy branch.
        if method == "GET" and url.endswith("/branches/main"):
            return "response", json.dumps({"commit": {"sha": "abc123head"}}).encode()
        # Create ref for side branch.
        if method == "POST" and url.endswith("/git/refs"):
            return "response", json.dumps({"ref": "refs/heads/astrolift/ci-workflow-..."}).encode()
        # PUT contents on the side branch — needs blob-sha probe first.
        if (
            method == "GET"
            and "/contents/.github/workflows/astrolift-ci.yml" in url
            and accept != "application/vnd.github.raw"
        ):
            return "error", _http_error(404, b"", url)
        if method == "PUT" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            return "response", json.dumps(
                {
                    "commit": {"sha": "siderev", "html_url": ""},
                    "content": {"path": ".github/workflows/astrolift-ci.yml"},
                }
            ).encode()
        # Open the PR.
        if method == "POST" and url.endswith("/pulls"):
            payload = json.loads(req.data.decode("utf-8"))
            assert payload["base"] == "main"
            assert payload["head"].startswith("astrolift/ci-workflow-")
            return "response", json.dumps({"html_url": "https://github.com/acme/api/pull/42"}).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    router.install(monkeypatch, handler)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "pr_opened"
    assert result.data.pr_url == "https://github.com/acme/api/pull/42"
    assert result.data.commit_sha is None


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_no_source_connection_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
):
    permission_resolver.grant(Permission.APP_UPDATE)

    def boom(*a, **kw):
        raise AssertionError("must not hit the network without a connection")

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "connection" in result.errors[0].message.lower()


def test_unsupported_source_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """Bitbucket / Gitea / other unsupported sources surface PRECONDITION."""
    permission_resolver.grant(Permission.APP_UPDATE)
    app_with_repo.source_kind = "bitbucket"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_permission_denied_without_app_update(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    def boom(*a, **kw):
        raise AssertionError("must not reach network without permission")

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_app_not_found(monkeypatch, permission_resolver, org):
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug="no-such-app"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_fetch_failure_maps_to_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    """Auth-failure on the initial fetch surfaces as PRECONDITION with
    a usable message; we don't go on to PUT and we don't crash on a 5xx."""
    permission_resolver.grant(Permission.APP_UPDATE)
    router = _Router()

    def handler(req):
        method = req.get_method()
        url = req.full_url
        if method == "GET" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            return "error", _http_error(401, b'{"message":"Bad credentials"}', url)
        raise AssertionError(f"must not continue past auth failure: {method} {url}")

    router.install(monkeypatch, handler)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "auth" in result.errors[0].message.lower() or "rejected" in result.errors[0].message.lower()


# ---------------------------------------------------------------------------
# Renderer unit cover — confirm the template substitution is exhaustive.
# ---------------------------------------------------------------------------


def test_render_substitutes_all_variables(settings, app_with_repo):
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com/"
    rendered = render_astrolift_ci_workflow(app_with_repo)
    # No remaining Jinja-style variable braces (only GitHub's ``${{ ... }}``).
    assert "{{ app_slug }}" not in rendered
    assert "{{ deploy_branch }}" not in rendered
    assert "{{ ecr_uri }}" not in rendered
    assert "{{ push_role_arn }}" not in rendered
    assert "{{ api_url }}" not in rendered
    # Substitutions landed.
    assert app_with_repo.slug in rendered
    assert "111111111111.dkr.ecr.us-west-2.amazonaws.com/acme/api" in rendered
    assert "arn:aws:iam::111111111111:role/astrolift-push-acme-api" in rendered
    assert "https://api.astrolift.example.com" in rendered
    # GitHub Actions expressions are NOT mistakenly substituted.
    assert "${{ github.sha }}" in rendered
    assert "${{ github.run_id }}" in rendered
    assert "${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}" in rendered
    # Header marker so downstream operators don't hand-edit.
    assert "Managed by Astrolift" in rendered


# ---------------------------------------------------------------------------
# GitLab CI renderer + sync (#735)
# ---------------------------------------------------------------------------


def test_render_gitlab_substitutes_all_variables(settings, app_with_gitlab_repo):
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com/"
    rendered = render_astrolift_gitlab_ci_workflow(app_with_gitlab_repo)
    assert "Managed by Astrolift" in rendered
    assert app_with_gitlab_repo.slug in rendered
    assert "111111111111.dkr.ecr.us-west-2.amazonaws.com/acme/api" in rendered
    assert "111111111111.dkr.ecr.us-west-2.amazonaws.com" in rendered
    assert "api.astrolift.example.com" in rendered
    assert "docker:24-dind" in rendered
    assert "$CI_COMMIT_SHA" in rendered
    assert "$ASTROLIFT_DEPLOY_TOKEN" in rendered
    assert "$ASTROLIFT_AWS_ACCESS_KEY_ID" in rendered
    assert ".gitlab-ci.yml" not in rendered  # path not embedded in the YAML body


def test_gitlab_creates_workflow_file_when_missing(
    monkeypatch,
    permission_resolver,
    org,
    app_with_gitlab_repo,
    gitlab_connection,
):
    permission_resolver.grant(Permission.APP_UPDATE)
    router = _Router()

    def handler(req):
        method = req.get_method()
        url = req.full_url
        # fetch_file: HEAD check for file → 404 (missing)
        if method == "HEAD" and ".gitlab-ci.yml" in url and "raw" not in url:
            return "error", _http_error(404, b"", url)
        # fetch_gitlab_file (raw): 404 → file missing
        if method == "GET" and "repository/files" in url and "raw" in url:
            return "error", _http_error(404, b"", url)
        # _is_gitlab_branch_protected: 404 → not protected
        if method == "GET" and "protected_branches" in url:
            return "error", _http_error(404, b"", url)
        # PUT HEAD: file doesn't exist yet on target branch
        if method == "HEAD" and ".gitlab-ci.yml" in url:
            return "error", _http_error(404, b"", url)
        # POST (create file)
        if method == "POST" and "repository/files" in url:
            return "response", json.dumps({"file_path": ".gitlab-ci.yml", "branch": "main"}).encode()
        # GET branch head for commit SHA
        if method == "GET" and "repository/branches/main" in url:
            return "response", json.dumps({"commit": {"id": "cafe1234abcd"}}).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    router.install(monkeypatch, handler, host="gitlab")

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_gitlab_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "created"
    assert result.data.commit_sha == "cafe1234abcd"
    assert result.data.pr_url is None


def test_gitlab_protected_branch_opens_mr(
    monkeypatch,
    permission_resolver,
    org,
    app_with_gitlab_repo,
    gitlab_connection,
):
    permission_resolver.grant(Permission.APP_UPDATE)
    router = _Router()

    def handler(req):
        method = req.get_method()
        url = req.full_url
        # fetch_gitlab_file (raw): 404 → file missing
        if method == "GET" and "repository/files" in url and "raw" in url:
            return "error", _http_error(404, b"", url)
        # _is_gitlab_branch_protected: 200 → protected
        if method == "GET" and "protected_branches" in url:
            return "response", json.dumps({"name": "main"}).encode()
        # _gitlab_create_branch
        if method == "POST" and "repository/branches" in url:
            return "response", json.dumps({"name": "astrolift/ci-workflow-hello-app"}).encode()
        # HEAD file on side branch: missing → POST
        if method == "HEAD" and "repository/files" in url:
            return "error", _http_error(404, b"", url)
        # POST create file on side branch
        if method == "POST" and "repository/files" in url:
            return "response", json.dumps(
                {"file_path": ".gitlab-ci.yml", "branch": "astrolift/ci-workflow-hello-app"}
            ).encode()
        # GET branch head SHA (for put_file's follow-up SHA fetch)
        if method == "GET" and "repository/branches" in url:
            return "response", json.dumps({"commit": {"id": "sidesha999"}}).encode()
        # POST merge_requests (open MR)
        if method == "POST" and "merge_requests" in url:
            payload = json.loads(req.data.decode("utf-8"))
            assert payload["target_branch"] == "main"
            assert "astrolift/ci-workflow-" in payload["source_branch"]
            return "response", json.dumps(
                {"web_url": "https://gitlab.com/acme/api/-/merge_requests/7", "iid": 7}
            ).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    router.install(monkeypatch, handler, host="gitlab")

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_gitlab_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "pr_opened"
    assert result.data.pr_url == "https://gitlab.com/acme/api/-/merge_requests/7"
    assert result.data.commit_sha is None


def test_gitlab_no_connection_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_gitlab_repo,
):
    """No GitLab connection in org → PRECONDITION (no network hit)."""
    permission_resolver.grant(Permission.APP_UPDATE)

    def boom(*a, **kw):
        raise AssertionError("must not hit the network without a connection")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.urllib.request.urlopen", boom)
    monkeypatch.setattr("astrolift_scm.providers.gitlab.urllib.request.urlopen", boom)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_gitlab_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "connection" in result.errors[0].message.lower()
