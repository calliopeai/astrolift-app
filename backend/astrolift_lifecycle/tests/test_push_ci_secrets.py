"""Tests for ``pushAstroliftCiSecretsToRepo`` (#383).

End-to-end coverage of the mutation surface: the GitHub public-key
fetch + each PUT-secret call is stubbed at ``urllib.request.urlopen``;
everything above the wire (personal-connection picker, deploy-token
rotation, libsodium sealing, permission gate, error mapping) runs
against real Postgres rows.

The sealed-box itself is exercised through ``pynacl`` against a
freshly-generated keypair so we round-trip the encryption rather than
mocking it — that catches subtle base64 / encoder bugs we'd miss with
a stubbed seal.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.deploy_tokens import issue_token
from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    PushCiSecretsToRepoInput,
)
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Local fixtures + helpers
# ---------------------------------------------------------------------------


def _info(user=None):
    """Minimal info-shaped object accepted by every mutation resolver.

    The push-secrets mutation reads ``info.context.request.user`` to
    select the viewer's personal connection. Tests that hit the
    happy path pass ``actor``; gate tests pass None."""
    request = SimpleNamespace(user=user)
    context = SimpleNamespace(request=request, user=user)
    return SimpleNamespace(context=context)


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def app_with_repo(app):
    """The base ``app`` fixture has no source repo / registry coordinates
    configured. Populate them so the push has something to send."""
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.deploy_branch = "main"
    app.registry_repo_uri = "111111111111.dkr.ecr.us-west-2.amazonaws.com/acme/api"
    app.push_role_ref = "arn:aws:iam::111111111111:role/astrolift-push-acme-api"
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "deploy_branch",
            "registry_repo_uri",
            "push_role_ref",
            "updated_at",
            "version",
        ]
    )
    return app


@pytest.fixture
def github_app_connection(org, monkeypatch):
    """Org GitHub App installation — the platform-write identity CI
    secrets now authenticate as (superseding the old per-user OAuth
    picker). ``user`` is NULL: this is an org-level credential, not a
    human's. ``installation_token`` is stubbed so no real JWT signing
    runs against the fake PEM bytes."""
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


@pytest.fixture
def github_keypair():
    """Fresh sealed-box keypair so the seal step actually round-trips.

    The fake urlopen returns the base64 public key on the public-key
    endpoint; tests can decrypt PUT bodies with the private key to
    assert the payload shape end-to-end."""
    from nacl.encoding import Base64Encoder
    from nacl.public import PrivateKey

    sk = PrivateKey.generate()
    return SimpleNamespace(
        private_key=sk,
        public_key=sk.public_key,
        public_key_b64=sk.public_key.encode(encoder=Base64Encoder).decode("ascii"),
        key_id="kid-test-1",
    )


def _http_error(code: int, body: bytes = b"", url: str = "https://example") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


class _Response:
    """Minimal context-manager mimicking urlopen's success shape."""

    def __init__(self, body: bytes = b"", status: int = 200):
        self._body = body
        self._status = status

    def __enter__(self):
        return SimpleNamespace(read=lambda: self._body, status=self._status)

    def __exit__(self, *_):
        return False


def _install_fake_github(monkeypatch, keypair, *, captured: dict):
    """Wire a urlopen stub that serves the public key and records PUTs.

    Returns nothing; the ``captured`` dict accumulates PUT bodies keyed
    by secret name + a counter of public-key fetches so tests can
    assert ordering."""
    captured.setdefault("puts", {})
    captured.setdefault("public_key_fetches", 0)

    def fake_urlopen(req, timeout=15):
        url = req.full_url
        method = req.get_method()
        if url.endswith("/actions/secrets/public-key"):
            captured["public_key_fetches"] += 1
            payload = {"key": keypair.public_key_b64, "key_id": keypair.key_id}
            return _Response(json.dumps(payload).encode("utf-8"), status=200)
        if "/actions/secrets/" in url and method == "PUT":
            name = url.rsplit("/", 1)[-1]
            captured["puts"][name] = json.loads(req.data.decode("utf-8"))
            return _Response(b"", status=204)
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        fake_urlopen,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_push_secrets_rotates_token_and_pushes_all_five(
    monkeypatch,
    settings,
    permission_resolver,
    org,
    actor,
    app_with_repo,
    github_app_connection,
    github_keypair,
):
    """End-to-end success: every secret PUT lands, the deploy token
    rotates, and the response carries the names + new last-4."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com/"

    # Seed a deploy token so the rotation path (rather than the
    # mint-fresh path) executes; we'll assert the hash changed.
    token_row, original_plaintext = issue_token(
        app=app_with_repo,
        name="ci-existing",
        by_user_id=actor.pk,
    )
    original_hash = token_row.token_hash

    captured: dict = {}
    _install_fake_github(monkeypatch, github_keypair, captured=captured)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload.repo == "acme/api"
    # Five canonical names — the workflow YAML keys off these exactly.
    assert payload.secret_names == [
        "ASTROLIFT_PUSH_ROLE_ARN",
        "ASTROLIFT_ECR_URI",
        "ASTROLIFT_APP_SLUG",
        "ASTROLIFT_API_URL",
        "ASTROLIFT_DEPLOY_TOKEN",
    ]
    assert payload.rotated_token_last_4 != original_plaintext[-4:]
    assert len(payload.rotated_token_last_4) == 4

    # The token row rotated in-place: hash changed, previous hash
    # revoked immediately (no grace window — the ``Push & rotate``
    # affordance promises immediate cutover in its confirm dialog).
    token_row.refresh_from_db()
    assert token_row.token_hash != original_hash
    assert token_row.previous_token_hash == ""
    assert token_row.previous_token_expires_at is None
    assert token_row.last_rotated_at is not None

    # All five secrets PUT, no extra calls.
    assert captured["public_key_fetches"] == 1
    assert set(captured["puts"]) == {
        "ASTROLIFT_PUSH_ROLE_ARN",
        "ASTROLIFT_ECR_URI",
        "ASTROLIFT_APP_SLUG",
        "ASTROLIFT_API_URL",
        "ASTROLIFT_DEPLOY_TOKEN",
    }

    # Freshness marker stamped (#1221) — validate compares GitHub's
    # per-secret updated_at against this; without it every validate
    # reported 0/N healthy forever.
    app_with_repo.refresh_from_db()
    assert app_with_repo.ci_secrets_pushed_at is not None


def test_push_secrets_sealed_values_round_trip(
    monkeypatch,
    settings,
    permission_resolver,
    org,
    actor,
    app_with_repo,
    github_app_connection,
    github_keypair,
):
    """The PUT body's ``encrypted_value`` is a libsodium sealed box of
    the right plaintext for each name. Round-trip with the keypair's
    private key to prove the seal is correct end-to-end."""
    from nacl.public import SealedBox

    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    captured: dict = {}
    _install_fake_github(monkeypatch, github_keypair, captured=captured)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors

    box = SealedBox(github_keypair.private_key)

    def _decrypt(name: str) -> str:
        body = captured["puts"][name]
        assert body["key_id"] == github_keypair.key_id
        return box.decrypt(base64.b64decode(body["encrypted_value"])).decode("utf-8")

    assert _decrypt("ASTROLIFT_PUSH_ROLE_ARN") == app_with_repo.push_role_ref
    assert _decrypt("ASTROLIFT_ECR_URI") == app_with_repo.registry_repo_uri
    assert _decrypt("ASTROLIFT_APP_SLUG") == app_with_repo.slug
    assert _decrypt("ASTROLIFT_API_URL") == "https://api.astrolift.example.com"
    # Deploy token plaintext: shape is alft_dt_<urlsafe>; we don't have
    # the plaintext in the response (only last4), so assert format.
    # #449: pre-canonicalisation we tolerated both ``alft_dt_`` and the
    # buggy ``alfdt_`` here; the mint now writes the canonical prefix
    # only, so the legacy fallback is gone.
    token_plain = _decrypt("ASTROLIFT_DEPLOY_TOKEN")
    assert token_plain.startswith("alft_dt_")
    assert token_plain[-4:] == result.data.rotated_token_last_4


def test_push_secrets_mints_token_when_none_exists(
    monkeypatch,
    settings,
    permission_resolver,
    org,
    actor,
    app_with_repo,
    github_app_connection,
    github_keypair,
):
    """No existing deploy-token rows → mint a fresh one (don't NPE on
    the rotation path)."""
    from astrolift_lifecycle.models import DeployToken

    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com"

    assert not DeployToken.objects.filter(registered_app=app_with_repo).exists()

    captured: dict = {}
    _install_fake_github(monkeypatch, github_keypair, captured=captured)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    minted = DeployToken.objects.filter(registered_app=app_with_repo).first()
    assert minted is not None
    assert minted.token_last_4 == result.data.rotated_token_last_4


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_push_secrets_no_app_install_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    actor,
    app_with_repo,
):
    """No org GitHub App installation → PRECONDITION about registering
    the App. A stale/present *personal* OAuth connection must NOT
    satisfy the platform-write path — that swap was the original bug
    (a human's expiring token authenticating a platform secret write).
    """
    permission_resolver.grant(Permission.APP_UPDATE)

    # Never hit the network when the gate trips.
    def boom(*_args, **_kw):
        raise AssertionError("must not reach GitHub without an App installation")

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        boom,
    )

    # Seed a viewer-owned github_oauth_user row — the connection the old
    # code would have (mis)used for the write. It must be ignored now.
    encrypted = encrypt_at_rest(b"gho_stale_user_token")
    SourceConnection.objects.create(
        organization=org,
        user=actor,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: actor",
        account_login="actor",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    msg = result.errors[0].message.lower()
    assert "github app" in msg and "not installed" in msg


def test_push_secrets_permission_denied_without_app_update(
    monkeypatch,
    permission_resolver,
    org,
    actor,
    app_with_repo,
    github_app_connection,
):
    """APP_READ alone is not enough; the mutation requires APP_UPDATE.
    No APP_UPDATE grant → PERMISSION_DENIED, never touches GitHub."""

    def boom(*_args, **_kw):
        raise AssertionError("must not reach GitHub without permission")

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        boom,
    )

    # Explicitly grant APP_READ to model the "read-only" operator and
    # prove it doesn't satisfy the APP_UPDATE gate.
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_push_secrets_public_key_fetch_failure_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    actor,
    app_with_repo,
    github_app_connection,
):
    """If the repo's public-key endpoint 404s, the push aborts cleanly
    before any PUTs or token rotation."""
    from astrolift_lifecycle.models import DeployToken

    permission_resolver.grant(Permission.APP_UPDATE)

    rotated_before = DeployToken.objects.filter(registered_app=app_with_repo).count()

    def fake_urlopen(req, timeout=15):
        if req.full_url.endswith("/actions/secrets/public-key"):
            raise _http_error(404, b'{"message":"Not Found"}', req.full_url)
        raise AssertionError("must not PUT secrets if public-key fetch failed")

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "github" in result.errors[0].message.lower()
    # No-op for the deploy-token rotation: we don't rotate when GitHub
    # can't take the new value.
    assert DeployToken.objects.filter(registered_app=app_with_repo).count() == rotated_before


def test_push_secrets_gitlab_source_pushes_project_variables(
    monkeypatch,
    permission_resolver,
    org,
    actor,
    app_with_repo,
):
    """GitLab-sourced app pushes the five Astrolift CI values to the
    project's CI/CD variables endpoint (#531). Shape mirrors the GitHub
    branch — same five names, deploy-token rotation, same return
    envelope — but each value lands via POST/PUT on the variables API
    rather than a sealed-box PUT."""
    permission_resolver.grant(Permission.APP_UPDATE)
    app_with_repo.source_kind = "gitlab"
    app_with_repo.source_repo = "acme-grp/api"
    app_with_repo.save(update_fields=["source_kind", "source_repo", "updated_at", "version"])

    # Personal GitLab connection — pick by user_id so the picker
    # accepts it.
    encrypted = encrypt_at_rest(b"glpat_personal_test_token")
    SourceConnection.objects.create(
        organization=org,
        user=actor,
        kind=SourceConnection.Kind.GITLAB_OAUTH_USER,
        display_name="GitLab: actor",
        account_login="actor",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )

    captured: dict = {"writes": [], "probes": 0}

    class _Resp:
        def __init__(self, body=b"", status=201):
            self._body = body
            self._status = status

        def __enter__(self):
            return SimpleNamespace(read=lambda: self._body, status=self._status)

        def __exit__(self, *_):
            return False

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        method = req.get_method()
        # The reach-probe is a GET against /variables?per_page=1.
        if method == "GET" and "/variables?per_page=1" in url:
            captured["probes"] += 1
            return _Resp(b"[]", status=200)
        # Variable writes — POST (create) succeeds; tests don't need
        # the PUT-fallback path here.
        if method == "POST" and url.endswith("/variables"):
            body = json.loads(req.data.decode("utf-8"))
            captured["writes"].append(body)
            return _Resp(b"", status=201)
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(
        "astrolift_scm.providers.gitlab.urllib.request.urlopen",
        fake_urlopen,
    )

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    # All five names landed.
    written_keys = {w["key"] for w in captured["writes"]}
    assert written_keys == {
        "ASTROLIFT_PUSH_ROLE_ARN",
        "ASTROLIFT_ECR_URI",
        "ASTROLIFT_APP_SLUG",
        "ASTROLIFT_API_URL",
        "ASTROLIFT_DEPLOY_TOKEN",
    }
    # Defaults: masked=True, protected=False.
    for w in captured["writes"]:
        assert w["masked"] is True
        assert w["protected"] is False
        assert w["variable_type"] == "env_var"
    # Reach-probe ran first.
    assert captured["probes"] == 1


def test_push_secrets_bitbucket_source_returns_precondition(
    monkeypatch,
    permission_resolver,
    org,
    actor,
    app_with_repo,
    github_app_connection,
):
    """A Bitbucket-sourced app surfaces a clean PRECONDITION envelope
    until the Bitbucket driver lands. Sibling of the old GitLab gap
    test."""
    permission_resolver.grant(Permission.APP_UPDATE)
    app_with_repo.source_kind = "bitbucket"
    app_with_repo.save(update_fields=["source_kind", "updated_at", "version"])

    def boom(*_args, **_kw):
        raise AssertionError("must not reach the host for an unsupported source kind")

    monkeypatch.setattr(
        "astrolift_scm.services.secrets.urllib.request.urlopen",
        boom,
    )

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "bitbucket" in result.errors[0].message.lower()


def test_push_secrets_app_not_found(
    monkeypatch,
    permission_resolver,
    org,
    actor,
):
    """Unknown app slug → NOT_FOUND with the right field marker."""
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug="no-such-app"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_push_secrets_rotation_normalizes_legacy_scope(
    monkeypatch,
    settings,
    permission_resolver,
    org,
    actor,
    app_with_repo,
    github_app_connection,
    github_keypair,
):
    """A legacy-scoped (``deploy``) token must come out of the CI push
    carrying ``app.deploy`` (#1222) — otherwise the rotated token the
    workflow just received keeps failing ci_deploy's scope check."""
    permission_resolver.grant(Permission.APP_UPDATE)
    settings.PLATFORM_API_URL = "https://api.astrolift.example.com/"

    token_row, _ = issue_token(
        app=app_with_repo,
        name="default",
        scopes=["deploy"],
        by_user_id=actor.pk,
    )

    captured: dict = {}
    _install_fake_github(monkeypatch, github_keypair, captured=captured)

    with _ctx(org):
        result = LifecycleMutation().push_astrolift_ci_secrets_to_repo(
            _info(actor),
            input=PushCiSecretsToRepoInput(app_slug=app_with_repo.slug),
        )

    assert result.ok, result.errors
    token_row.refresh_from_db()
    assert "app.deploy" in token_row.scopes
    assert "deploy" in token_row.scopes  # legacy grant preserved, not replaced
