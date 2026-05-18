"""Tests for the manifest editor mutations (#277).

Covers update_manifest (stage), sync_manifest_from_repo (refresh),
push_manifest_to_repo (open PR). The PR-creation side talks to SCM
providers — those calls are stubbed at ``urllib.request.urlopen``
(same pattern used in ``astrolift_scm/tests/test_push_ci_workflow.py``)
so the tests run hermetically while still exercising the real
provider dispatch path.

#535 / #536 closed the TODO that used to return ``scm_pending`` with
an empty pr_url; ``push_manifest_to_repo`` now opens a real PR + the
``sync_manifest_from_repo`` mutation re-reads from the source repo
via the SCM provider's fetch_file path."""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    PushManifestToRepoInput,
    RegistryMutation,
    SyncManifestFromRepoInput,
    UpdateManifestInput,
)
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold(
    *,
    manifest_raw: str = "",
    manifest_hash: str = "",
    source_repo: str = "acme/api",
    source_kind: str = "github",
):
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        manifest_raw=manifest_raw,
        manifest_hash=manifest_hash,
        last_synced_hash=manifest_hash,
        source_kind=source_kind,
        source_repo=source_repo,
        manifest_path="astrolift.toml",
        default_branch="main",
        deploy_branch="main",
    )
    return org, app


def _scaffold_github_conn(org, *, kind=None):
    """Encrypted-at-rest GitHub user-OAuth connection — mirrors the
    test fixture in astrolift_scm/tests/test_push_ci_workflow.py."""
    encrypted = encrypt_at_rest(b"gho_test_user_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=kind or SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


class _CMResponse:
    """urllib-style context manager that hands back a fixed JSON body
    + an optional status code. Lifted from
    ``astrolift_scm.tests.test_push_ci_workflow``."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=self.status)

    def __exit__(self, *_):
        return False


def _http_response(payload=None, *, status: int = 200) -> _CMResponse:
    return _CMResponse(json.dumps(payload or {}).encode("utf-8"), status=status)


def _http_error(url: str, code: int, reason: str = "", body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, reason, {}, io.BytesIO(body))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


_VALID_TOML = """
astrolift_version = 1
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
"""


def test_update_manifest_stages_to_staged_buffer(permission_resolver):
    org, app = _scaffold(manifest_raw="prior", manifest_hash="abc")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest=_VALID_TOML,
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    # manifest_raw is the source-of-truth; staging holds the draft
    assert app.manifest_raw == "prior"
    assert app.manifest_raw_staged.strip() == _VALID_TOML.strip()


def test_update_manifest_with_match_clears_staging_buffer(
    permission_resolver,
):
    """If the user's edit converges back to the source-of-truth,
    the staging buffer is cleared so the UI reads as in_sync."""
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    app.manifest_raw_staged = "uncommitted"
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest=_VALID_TOML,
            ),
        )

    assert result.ok
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_update_manifest_rejects_invalid_toml(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest="this is = not valid [toml ",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "rawManifest"
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_update_manifest_empty_clears_staging(permission_resolver):
    """Empty input is valid + clears the staging buffer (= 'discard')."""
    org, app = _scaffold(manifest_raw=_VALID_TOML)
    app.manifest_raw_staged = "leftover-draft"
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(id=str(app.guid), raw_manifest=""),
        )

    assert result.ok
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_update_manifest_requires_permission():
    org, app = _scaffold()
    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id=str(app.guid),
                raw_manifest=_VALID_TOML,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_sync_from_repo_clears_staging_when_repo_matches(permission_resolver, monkeypatch):
    """When the staged buffer matches the repo's content, the service
    layer drops the buffer + anchors. Stub the SCM fetch to return
    the staged TOML so the diff-and-apply path resolves to ``applied``
    (or ``in_sync`` if hashes match) rather than ``fetch_failed``."""
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    _scaffold_github_conn(org)
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    def _stub_fetch(connection, repo_full_name, path, ref):
        return _VALID_TOML

    monkeypatch.setattr(
        "astrolift_registry.services.manifest_sync._default_fetch",
        _stub_fetch,
    )

    with _ctx(org):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(),
            input=SyncManifestFromRepoInput(id=str(app.guid)),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_sync_from_repo_anchors_last_synced_hash(permission_resolver, monkeypatch):
    """A successful resync writes ``last_synced_hash`` from the
    normalized repo content's hash. The service computes the hash on
    the normalized form, so we assert it's non-empty rather than
    pinning a specific value."""
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    _scaffold_github_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    monkeypatch.setattr(
        "astrolift_registry.services.manifest_sync._default_fetch",
        lambda *a, **kw: _VALID_TOML,
    )

    with _ctx(org):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(),
            input=SyncManifestFromRepoInput(id=str(app.guid)),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.last_synced_hash, "anchor must advance after a successful resync"


def test_sync_from_repo_returns_failure_when_no_connection(permission_resolver):
    """No SourceConnection in the org → mutation returns
    SCM_FETCH_FAILED rather than silently anchoring (the old TODO
    behaviour returned ``ok=True`` on the same input)."""
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(),
            input=SyncManifestFromRepoInput(id=str(app.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "SCM_FETCH_FAILED"
    assert "no active source connection" in result.errors[0].message


def test_sync_from_repo_returns_failure_on_diverged(permission_resolver, monkeypatch):
    """A staged buffer + repo content that differs from the DB
    surfaces as SCM_DIVERGED so the UI can warn before clobbering."""
    org, app = _scaffold(manifest_raw=_VALID_TOML, manifest_hash="abc")
    _scaffold_github_conn(org)
    app.manifest_raw_staged = "operator's pending draft"
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    # Repo has a different content from both the DB anchor + the
    # staged buffer — the divergence guard fires.
    other_toml = _VALID_TOML.replace('name = "hello"', 'name = "hello-renamed"')
    monkeypatch.setattr(
        "astrolift_registry.services.manifest_sync._default_fetch",
        lambda *a, **kw: other_toml,
    )

    with _ctx(org):
        result = RegistryMutation().sync_manifest_from_repo(
            _info(),
            input=SyncManifestFromRepoInput(id=str(app.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "SCM_DIVERGED"


def test_push_with_no_staged_returns_nothing_to_push(
    permission_resolver,
):
    org, app = _scaffold(manifest_raw=_VALID_TOML)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(id=str(app.guid)),
        )

    assert result.ok
    assert result.data.note == "nothing_to_push"


def _stub_github_push_happy(monkeypatch, *, pr_number=42, pr_html_url=None):
    """Install a happy-path GitHub HTTP stub for the push flow:

    1. GET (file exists probe) — 404 (first-time write to that branch)
    2. PUT (write file) — 200 + commit SHA
    3. POST (open PR) — 201 + html_url + number

    Returns the list of requests seen so the test can assert against
    the call sequence + bodies."""
    seen: list = []
    pr_url = pr_html_url or f"https://github.com/acme/api/pull/{pr_number}"

    def fake_urlopen(req, timeout=10):
        seen.append(req)
        method = req.get_method()
        if method == "GET":
            raise _http_error(req.full_url, 404, "Not Found", b"missing")
        if method == "PUT":
            return _http_response(
                {
                    "commit": {"sha": "newcommitsha"},
                    "content": {
                        "path": "astrolift.toml",
                        "html_url": "https://github.com/acme/api/blob/branch/astrolift.toml",
                    },
                }
            )
        if method == "POST":
            return _http_response({"html_url": pr_url, "number": pr_number})
        raise AssertionError(f"unexpected HTTP method {method}")

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )
    return seen, pr_url


def test_push_with_staged_opens_pr(permission_resolver, monkeypatch):
    """End-to-end: staged TOML + happy-path SCM → mutation returns
    the PR url + the branch name + ``note='pushed'``."""
    org, app = _scaffold(manifest_raw="prior")
    _scaffold_github_conn(org)
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    seen, pr_url = _stub_github_push_happy(monkeypatch, pr_number=42)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(id=str(app.guid)),
        )

    assert result.ok, result.errors
    assert result.data.note == "pushed"
    assert result.data.pr_url == pr_url
    assert result.data.branch_name == "astrolift/manifest-hello-app"
    # GET probe + PUT file + POST PR — three calls in that order.
    assert [r.get_method() for r in seen] == ["GET", "PUT", "POST"]


def test_push_with_explicit_branch_uses_it(permission_resolver, monkeypatch):
    org, app = _scaffold(manifest_raw="prior")
    _scaffold_github_conn(org)
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    seen, _ = _stub_github_push_happy(monkeypatch)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(
                id=str(app.guid),
                branch_name="my-feature-branch",
            ),
        )

    assert result.ok, result.errors
    assert result.data.branch_name == "my-feature-branch"
    # The PUT body carries the explicit branch — make sure the
    # mutation passed it through verbatim rather than re-deriving.
    put_req = next(r for r in seen if r.get_method() == "PUT")
    put_body = json.loads(put_req.data.decode("utf-8"))
    assert put_body["branch"] == "my-feature-branch"


def test_push_pr_body_carries_title_and_default_body(permission_resolver, monkeypatch):
    """Caller can override pr_title + pr_body; defaults are used
    otherwise. The POST body for the PR carries title + body + base."""
    org, app = _scaffold(manifest_raw="prior")
    _scaffold_github_conn(org)
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    seen, _ = _stub_github_push_happy(monkeypatch)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(
                id=str(app.guid),
                pr_title="Manual: tweak manifest",
                pr_body="See ticket #123 for context.",
            ),
        )

    assert result.ok, result.errors
    post_req = next(r for r in seen if r.get_method() == "POST")
    post_body = json.loads(post_req.data.decode("utf-8"))
    assert post_body["title"] == "Manual: tweak manifest"
    assert post_body["body"] == "See ticket #123 for context."
    assert post_body["base"] == "main"
    assert post_body["head"] == "astrolift/manifest-hello-app"


def test_push_returns_failure_when_no_connection(permission_resolver):
    """No SourceConnection → SCM_PUSH_FAILED with a reconnect hint."""
    org, app = _scaffold(manifest_raw="prior")
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(id=str(app.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "SCM_PUSH_FAILED"
    assert "no active source connection" in result.errors[0].message


def test_push_returns_failure_on_provider_auth_error(permission_resolver, monkeypatch):
    """Provider 401 maps to SCM_PUSH_FAILED — mutation envelope
    contract, never raises."""
    org, app = _scaffold(manifest_raw="prior")
    _scaffold_github_conn(org)
    app.manifest_raw_staged = _VALID_TOML
    app.save(update_fields=["manifest_raw_staged"])
    permission_resolver.grant(Permission.APP_UPDATE)

    def fake_urlopen(req, timeout=10):
        raise _http_error(req.full_url, 401, "Unauthorized", b"bad token")

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = RegistryMutation().push_manifest_to_repo(
            _info(),
            input=PushManifestToRepoInput(id=str(app.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "SCM_PUSH_FAILED"
    assert "AUTH_FAILED" in result.errors[0].message


def test_update_manifest_unknown_app_returns_not_found(
    permission_resolver,
):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = RegistryMutation().update_manifest(
            _info(),
            input=UpdateManifestInput(
                id="00000000-0000-0000-0000-000000000000",
                raw_manifest=_VALID_TOML,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
