"""Tests for ``pushAstroliftCiWorkflowToRepo`` (#384).

End-to-end coverage of the mutation surface: every host HTTP call
(fetch existing file, branch-protection probe, PUT contents, PR
create) is stubbed at ``urllib.request.urlopen``; everything above
the wire (connection picker, template rendering, status discrimination,
permission gate, error mapping) runs against real Postgres rows.

Matrix:
  * file-missing → ``created`` with commit_sha
  * file diverged → ``updated`` with commit_sha
  * file matches → ``in_sync`` (no PUT issued)
  * protected branch → ``pr_opened`` with pr_url (PR path exercised end-to-end)
  * no connection in org → PRECONDITION
  * GitLab-sourced app → PRECONDITION (NotImplementedError surfaces clean)
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
from astrolift_scm.services.workflow_sync import render_astrolift_ci_workflow
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

    def install(self, monkeypatch, handler):
        def fake(req, timeout=15):
            self.calls.append((req.get_method(), req.full_url))
            self.bodies.append(req.data)
            kind, value = handler(req)
            if kind == "error":
                raise value
            return _Response(body=value if isinstance(value, (bytes, bytearray)) else b"")

        # workflow_sync uses urlopen via its module-level import.
        monkeypatch.setattr(
            "astrolift_scm.services.workflow_sync.urllib.request.urlopen",
            fake,
        )
        # Provider-side PUT / GET (for fetch_file, put_file) goes through
        # the github provider; patch its urlopen too.
        monkeypatch.setattr(
            "astrolift_scm.providers.github.urllib.request.urlopen",
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


def test_updates_workflow_file_when_diverged(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    permission_resolver.grant(Permission.APP_UPDATE)
    router = _Router()
    drift_body = b"# stale workflow that doesn't match the template\nname: old\n"

    def handler(req):
        method = req.get_method()
        url = req.full_url
        accept = req.get_header("Accept") or ""
        if method == "GET" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            if accept == "application/vnd.github.raw":
                return "response", drift_body  # fetch_file path
            # _github_existing_sha path on PUT — needs blob sha.
            return "response", json.dumps({"sha": "oldblob"}).encode()
        if method == "GET" and "/branches/main/protection" in url:
            return "error", _http_error(404, b"", url)
        if method == "PUT" and "/contents/.github/workflows/astrolift-ci.yml" in url:
            return "response", json.dumps(
                {
                    "commit": {
                        "sha": "newcafe1234",
                        "html_url": "https://github.com/acme/api/commit/newcafe1234",
                    },
                    "content": {"path": ".github/workflows/astrolift-ci.yml"},
                }
            ).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    router.install(monkeypatch, handler)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    assert result.data.status == "updated"
    assert result.data.commit_sha == "newcafe1234"


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


def test_gitlab_source_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    app_with_repo,
    github_connection,
):
    permission_resolver.grant(Permission.APP_UPDATE)
    app_with_repo.source_kind = "gitlab"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_workflow_to_repo(
            _info(),
            input=PushCiWorkflowToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "github" in result.errors[0].message.lower()


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
