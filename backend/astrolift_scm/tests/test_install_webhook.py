"""
Tests for ``install_scm_webhook`` (#293).

Covers per-provider HTTP shape (network patched out), the
GitHub-App short-circuit path, and the post-install persistence on
``ScmWebhookInstallation``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.models import ScmWebhookInstallation, SourceConnection
from astrolift_scm.schema.mutations import (
    InstallScmWebhookInput,
    ScmMutation,
)
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-wh")


@pytest.fixture
def github_pat_connection(org):
    encrypted = encrypt_at_rest(b"ghp_xxx")
    return SourceConnection.objects.create(
        organization=org,
        kind="github_pat",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


@pytest.fixture
def gitlab_pat_connection(org):
    encrypted = encrypt_at_rest(b"glpat_xxx")
    return SourceConnection.objects.create(
        organization=org,
        kind="gitlab_pat",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


@pytest.fixture
def github_app_connection(org):
    encrypted = encrypt_at_rest(b"fake-pem-bytes")
    return SourceConnection.objects.create(
        organization=org,
        kind="github_app_install",
        account_login="acme",
        installation_id="987",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None)))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 201):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_install_github_webhook_posts_and_persists_hook_id(
    org, github_pat_connection, monkeypatch, permission_resolver, settings
):
    settings.APP_BASE_URL = "https://astrolift.test"
    permission_resolver.grant(Permission.SCM_CONNECT)

    captured: dict = {}

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"id": 42, "url": req.full_url, "active": True})

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    mut = ScmMutation()
    with _tenant(org):
        result = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=github_pat_connection.guid,
                repo_full_name="acme/widgets",
            ),
        )
    assert result.ok, result.errors
    assert result.data.hook_id == "42"
    assert result.data.provider_short_circuited is False
    assert captured["url"].endswith("/repos/acme/widgets/hooks")
    assert captured["method"] == "POST"
    assert captured["body"]["config"]["url"].endswith(
        f"/app/auth1/scm/github/webhook/{github_pat_connection.guid}/"
    )
    # Secret got minted + persisted on the connection.
    github_pat_connection.refresh_from_db()
    assert github_pat_connection.webhook_secret_ciphertext

    row = ScmWebhookInstallation.objects.get(source_connection=github_pat_connection)
    assert row.hook_id == "42"
    assert row.repo_full_name == "acme/widgets"


def test_install_github_webhook_idempotent_overwrites_row(
    org, github_pat_connection, monkeypatch, permission_resolver, settings
):
    settings.APP_BASE_URL = "https://astrolift.test"
    permission_resolver.grant(Permission.SCM_CONNECT)

    calls = [
        _FakeResponse({"id": 100, "active": True}),
        _FakeResponse({"id": 101, "active": True}),
    ]

    def fake_urlopen(req, timeout=15):
        return calls.pop(0)

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    mut = ScmMutation()
    with _tenant(org):
        first = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=github_pat_connection.guid,
                repo_full_name="acme/widgets",
            ),
        )
        second = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=github_pat_connection.guid,
                repo_full_name="acme/widgets",
            ),
        )
    assert first.ok and second.ok
    # Same row, hook_id updated to the latest install response.
    assert first.data.id == second.data.id
    assert second.data.hook_id == "101"


def test_install_webhook_short_circuits_for_github_app_install(
    org, github_app_connection, permission_resolver, settings
):
    settings.APP_BASE_URL = "https://astrolift.test"
    permission_resolver.grant(Permission.SCM_CONNECT)

    mut = ScmMutation()
    with _tenant(org):
        result = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=github_app_connection.guid,
                repo_full_name="acme/widgets",
            ),
        )
    assert result.ok, result.errors
    assert result.data.provider_short_circuited is True
    assert result.data.hook_id == ""
    row = ScmWebhookInstallation.objects.get(source_connection=github_app_connection)
    assert row.provider_short_circuited is True


def test_installation_includes_repo_paginates_until_found(github_app_connection, monkeypatch):
    """``github_installation_includes_repo`` walks every page of
    ``/installation/repositories`` before answering — a repo on page 2
    must be found, so the webhook step's "App delivers" claim is honest
    even for installations with >100 repos."""
    from astrolift_scm.providers.github import github_installation_includes_repo

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: "ghs_fake",
    )

    # Page 1: a full page (100) of non-matching repos → forces a page 2.
    # Page 2: a short page that contains the target.
    page1 = [{"full_name": f"acme/repo-{i}"} for i in range(100)]
    page2 = [{"full_name": "acme/target"}, {"full_name": "acme/tail"}]
    pages = iter([page1, page2])
    seen_pages: list[str] = []

    def fake_urlopen(req, timeout=10):
        seen_pages.append(req.full_url)
        repos = next(pages)
        return _FakeResponse({"total_count": 102, "repositories": repos}, status=200)

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    assert github_installation_includes_repo(github_app_connection, repo_full_name="acme/target") is True
    assert len(seen_pages) == 2
    assert "page=2" in seen_pages[1]


def test_installation_includes_repo_false_on_short_page_without_extra_call(
    github_app_connection, monkeypatch
):
    """A single short page that excludes the target answers False without
    fetching a second page — the App is not installed on that repo."""
    from astrolift_scm.providers.github import github_installation_includes_repo

    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: "ghs_fake",
    )

    calls: list[str] = []

    def fake_urlopen(req, timeout=10):
        calls.append(req.full_url)
        return _FakeResponse(
            {"total_count": 1, "repositories": [{"full_name": "acme/other"}]},
            status=200,
        )

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    assert github_installation_includes_repo(github_app_connection, repo_full_name="acme/target") is False
    assert len(calls) == 1


def test_install_gitlab_webhook_posts_to_projects_hooks(
    org, gitlab_pat_connection, monkeypatch, permission_resolver, settings
):
    settings.APP_BASE_URL = "https://astrolift.test"
    permission_resolver.grant(Permission.SCM_CONNECT)

    captured: dict = {}

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"id": 999, "url": "x"})

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    mut = ScmMutation()
    with _tenant(org):
        result = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=gitlab_pat_connection.guid,
                repo_full_name="acme/widgets",
            ),
        )
    assert result.ok, result.errors
    assert result.data.hook_id == "999"
    assert "/api/v4/projects/" in captured["url"]
    assert "/hooks" in captured["url"]
    assert captured["body"]["push_events"] is True


def test_install_webhook_surfaces_auth_failure_cleanly(
    org, github_pat_connection, monkeypatch, permission_resolver, settings
):
    settings.APP_BASE_URL = "https://astrolift.test"
    permission_resolver.grant(Permission.SCM_CONNECT)

    def fake_urlopen(req, timeout=15):
        raise HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        fake_urlopen,
    )

    mut = ScmMutation()
    with _tenant(org):
        result = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=github_pat_connection.guid,
                repo_full_name="acme/widgets",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "AUTH_FAILED"


def test_install_webhook_refuses_oauth_app_config_row(org, monkeypatch, permission_resolver, settings):
    """OAuth-app rows hold the host's *client_secret*, not a user token —
    you can't drive an API call with them."""
    settings.APP_BASE_URL = "https://astrolift.test"
    permission_resolver.grant(Permission.SCM_CONNECT)
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_oauth_app",
        oauth_client_id="abc123",
        account_login="",  # the marker for is_oauth_app_config
        secret_backend_kind="local_fernet",
        secret_ciphertext=b"",
        is_active=True,
    )

    mut = ScmMutation()
    with _tenant(org):
        result = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=conn.guid,
                repo_full_name="acme/widgets",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_install_webhook_requires_scm_connect_permission(
    org, github_pat_connection, permission_resolver, settings
):
    """Caller without scm.connect is denied; mutation_audit converts the
    PermissionDenied raise into a PERMISSION_DENIED failure envelope."""
    settings.APP_BASE_URL = "https://astrolift.test"

    mut = ScmMutation()
    with _tenant(org):
        result = mut.install_scm_webhook(
            _info(),
            input=InstallScmWebhookInput(
                connection_id=github_pat_connection.guid,
                repo_full_name="acme/widgets",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
