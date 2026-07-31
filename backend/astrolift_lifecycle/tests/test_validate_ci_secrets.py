"""Tests for ``validateAstroliftCiSecrets`` (#693).

Read-only counterpart to ``pushAstroliftCiSecretsToRepo`` (#383). Same
auth path (the org GitHub App installation) but hits the
``GET /repos/{owner}/{repo}/actions/secrets`` endpoint and reports a
per-secret presence map back to the FE.

Wire-level stub via urlopen so the test runs without network. The
permission gate + connection picker + error mapping run against real
Postgres rows.
"""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    ValidateAstroliftCiSecretsInput,
)
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    request = SimpleNamespace(user=user)
    context = SimpleNamespace(request=request, user=user)
    return SimpleNamespace(context=context)


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


_FIVE = (
    "ASTROLIFT_PUSH_ROLE_ARN",
    "ASTROLIFT_ECR_URI",
    "ASTROLIFT_APP_SLUG",
    "ASTROLIFT_API_URL",
    "ASTROLIFT_DEPLOY_TOKEN",
)


@pytest.fixture
def app_with_repo(app):
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.save(update_fields=["source_kind", "source_repo", "updated_at", "version"])
    return app


@pytest.fixture
def github_app_connection(org, monkeypatch):
    """Org GitHub App installation — validate now reads under the same
    platform identity the push side writes with. ``installation_token``
    is stubbed so no real JWT signing runs against the fake PEM."""
    encrypted = encrypt_at_rest(b"fake-app-pem-bytes")
    conn = SourceConnection.objects.create(
        organization=org,
        user=None,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App: acme",
        account_login="acme",
        installation_id="424242",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    monkeypatch.setattr(
        "astrolift_scm.providers.github_app.installation_token",
        lambda connection: "ghs_fake_installation_token",
    )
    return conn


class _Headers(dict):
    """Mimics urllib's HTTPMessage enough for our code path."""

    def get(self, key, default=""):  # type: ignore[override]
        return super().get(key, default)


class _Response:
    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None):
        self._body = body
        self._status = status
        self._headers = _Headers(headers or {})

    def __enter__(self):
        return SimpleNamespace(
            read=lambda: self._body,
            status=self._status,
            headers=self._headers,
        )

    def __exit__(self, *_):
        return False


def _install_fake_list(monkeypatch, *, secrets: list[dict]):
    """Stub urlopen to return ``secrets`` as the GitHub list-secrets
    response.  Each dict shape: ``{"name": str, "updated_at": str}``.
    """

    payload = {"total_count": len(secrets), "secrets": secrets}

    def fake_urlopen(req, timeout=15):
        url = req.full_url
        if url.endswith("/actions/secrets") or "/actions/secrets?" in url:
            return _Response(json.dumps(payload).encode("utf-8"), status=200)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        fake_urlopen,
    )


# ---- Happy paths --------------------------------------------------


def test_validate_returns_all_five_when_all_set(
    monkeypatch, permission_resolver, org, app_with_repo, actor, github_app_connection
):
    permission_resolver.grant(Permission.APP_READ)
    _install_fake_list(
        monkeypatch,
        secrets=[{"name": name, "updated_at": "2026-05-10T00:00:00Z"} for name in _FIVE],
    )
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app_with_repo.slug),
        )
    assert result.ok, result.errors
    assert result.data.repo == "acme/api"
    by_name = {r.secret_name: r for r in result.data.results}
    for name in _FIVE:
        assert by_name[name].is_set is True, name
    # Without a baseline (`ci_secrets_pushed_at`) is_current is unknown.
    assert all(r.is_current is None for r in result.data.results)


def test_validate_returns_missing_rows_for_unset_secrets(
    monkeypatch, permission_resolver, org, app_with_repo, actor, github_app_connection
):
    permission_resolver.grant(Permission.APP_READ)
    _install_fake_list(
        monkeypatch,
        secrets=[
            {"name": "ASTROLIFT_PUSH_ROLE_ARN", "updated_at": "2026-05-10T00:00:00Z"},
            {"name": "ASTROLIFT_ECR_URI", "updated_at": "2026-05-10T00:00:00Z"},
            # missing the other three
        ],
    )
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app_with_repo.slug),
        )
    assert result.ok, result.errors
    by_name = {r.secret_name: r for r in result.data.results}
    assert by_name["ASTROLIFT_PUSH_ROLE_ARN"].is_set is True
    assert by_name["ASTROLIFT_DEPLOY_TOKEN"].is_set is False
    assert by_name["ASTROLIFT_DEPLOY_TOKEN"].is_current is False


# ---- Failure paths ------------------------------------------------


def test_validate_unknown_app_returns_not_found(permission_resolver, org, actor):
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug="ghost-app"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_validate_no_app_install_returns_precondition(permission_resolver, org, app_with_repo, actor):
    """No org GitHub App installation = PRECONDITION about registering
    the App. Validate reads under the platform identity now, so a
    missing App (not a missing personal connection) is the gate."""
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app_with_repo.slug),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    msg = result.errors[0].message.lower()
    assert "github app" in msg and "not installed" in msg


def test_validate_unsupported_source_returns_precondition(permission_resolver, org, app, actor):
    """A GitLab app should report UNSUPPORTED_SOURCE — the validate
    surface is GitHub-only today."""
    permission_resolver.grant(Permission.APP_READ)
    app.source_kind = "gitlab"
    app.source_repo = "acme/api"
    app.save(update_fields=["source_kind", "source_repo", "updated_at", "version"])
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app.slug),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "github-only" in result.errors[0].message.lower()


def test_validate_no_repo_returns_precondition(permission_resolver, org, app, actor):
    permission_resolver.grant(Permission.APP_READ)
    # app from fixture has no source_repo by default
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app.slug),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_validate_github_auth_failure_returns_precondition(
    monkeypatch, permission_resolver, org, app_with_repo, actor, github_app_connection
):
    """A 401 from GitHub should bubble as PRECONDITION with the AUTH_FAILED
    message text — caller can re-prompt the operator to reconnect."""
    permission_resolver.grant(Permission.APP_READ)

    def fake_urlopen(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        fake_urlopen,
    )
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app_with_repo.slug),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_validate_requires_app_read(monkeypatch, org, app_with_repo, actor, github_app_connection):
    """Permission gate denies without app.read."""
    with _ctx(org):
        result = LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app_with_repo.slug),
        )
    assert not result.ok


# ---- freshness against ci_secrets_pushed_at (#1221) ----------------


def _validate(org, actor, app):
    with _ctx(org):
        return LifecycleMutation().validate_astrolift_ci_secrets(
            _info(actor),
            input=ValidateAstroliftCiSecretsInput(app_slug=app.slug),
        )


def test_validate_is_current_true_when_pushed_before_update(
    monkeypatch, permission_resolver, org, app_with_repo, actor, github_app_connection
):
    import datetime as dt

    permission_resolver.grant(Permission.APP_READ)
    app_with_repo.ci_secrets_pushed_at = dt.datetime(2026, 5, 9, 23, 59, 0, tzinfo=dt.UTC)
    app_with_repo.save(update_fields=["ci_secrets_pushed_at", "updated_at", "version"])
    _install_fake_list(
        monkeypatch,
        secrets=[{"name": name, "updated_at": "2026-05-10T00:00:00Z"} for name in _FIVE],
    )
    result = _validate(org, actor, app_with_repo)
    assert result.ok, result.errors
    assert all(r.is_current is True for r in result.data.results)


def test_validate_is_current_true_within_same_second_tolerance(
    monkeypatch, permission_resolver, org, app_with_repo, actor, github_app_connection
):
    """GitHub's updated_at is second-granularity — a push stamped a moment
    AFTER the second-truncated updated_at must still read as current."""
    import datetime as dt

    permission_resolver.grant(Permission.APP_READ)
    app_with_repo.ci_secrets_pushed_at = dt.datetime(2026, 5, 10, 0, 0, 0, 900000, tzinfo=dt.UTC)
    app_with_repo.save(update_fields=["ci_secrets_pushed_at", "updated_at", "version"])
    _install_fake_list(
        monkeypatch,
        secrets=[{"name": name, "updated_at": "2026-05-10T00:00:00Z"} for name in _FIVE],
    )
    result = _validate(org, actor, app_with_repo)
    assert result.ok, result.errors
    assert all(r.is_current is True for r in result.data.results)


def test_validate_is_current_false_when_secret_predates_push(
    monkeypatch, permission_resolver, org, app_with_repo, actor, github_app_connection
):
    import datetime as dt

    permission_resolver.grant(Permission.APP_READ)
    app_with_repo.ci_secrets_pushed_at = dt.datetime(2026, 5, 10, 12, 0, 0, tzinfo=dt.UTC)
    app_with_repo.save(update_fields=["ci_secrets_pushed_at", "updated_at", "version"])
    _install_fake_list(
        monkeypatch,
        secrets=[{"name": name, "updated_at": "2026-05-10T00:00:00Z"} for name in _FIVE],
    )
    result = _validate(org, actor, app_with_repo)
    assert result.ok, result.errors
    assert all(r.is_current is False for r in result.data.results)
