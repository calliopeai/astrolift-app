"""A repository is served only by the GitHub App installation on its owner (#2576).

The same App is installed on two GitHub accounts: ConflictHQ, the
organization's first connection and the one ranking used to fall back to,
and calliopeai. An app built from calliopeai/cykick runs ``astro ci setup``,
which is the webhook, CI secrets and CI workflow mutations. A local HTTP
server plays GitHub for both installations and records which installation
each request authenticated as. Connections, apps and audit rows are real
Postgres rows.
"""

from __future__ import annotations

import base64
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone
from nacl.public import PrivateKey

from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.schema.mutations import (
    InstallSourceWebhookInput,
    LifecycleMutation,
    PushCiSecretsToRepoInput,
    PushCiWorkflowToRepoInput,
)
from astrolift_registry.models import RegisteredApp
from astrolift_registry.services import manifest_sync
from astrolift_scm.models import SourceConnection
from astrolift_scm.schema.mutations import ConnectExistingGithubAppInput, ScmMutation
from astrolift_scm.services import webhooks, workflow_sync
from astrolift_scm.services.connection_resolver import (
    ORG_REPO_WRITE,
    PLATFORM_REPO_WRITE,
    ConnectionResolutionError,
    resolve_connection,
)
from astrolift_scm.services.workflow_sync import github_workflow_path_for
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

APP_ID = "2576"
CLIENT_ID = "Iv23liAstroliftConflict"
REPO = "calliopeai/cykick"
CONFLICTHQ, CALLIOPEAI = "1001", "2002"
ACCOUNTS = {CONFLICTHQ: "ConflictHQ", CALLIOPEAI: "calliopeai"}
# A different App's installation on calliopeai, without the repo: the live
# setup's active calliopeai connection used another App (#2577).
OTHER_APP_ON_CALLIOPEAI = "3003"
INSTALLED_REPOS = {
    CONFLICTHQ: ["ConflictHQ/site"],
    CALLIOPEAI: [REPO],
    OTHER_APP_ON_CALLIOPEAI: ["calliopeai/site"],
}


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def github():
    """GitHub for an App installed on ConflictHQ and calliopeai, plus another
    App's installation on calliopeai.

    An installation token sees only its installation's repositories; any
    other repository answers 404, as GitHub does. ``requests`` holds
    ``(method, path, installation)``, with ``"app"`` for App-JWT calls.
    """
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sealing = PrivateKey.generate()
    state = SimpleNamespace(requests=[], files={}, secrets={})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body=None, *, raw=None):
            payload = raw.encode() if raw is not None else json.dumps({} if body is None else body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def handle_request(self):
            path = unquote(urlsplit(self.path).path)
            auth = self.headers.get("Authorization", "")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if path == "/app" or path.startswith("/app/"):
                claims = jwt.decode(auth.removeprefix("Bearer "), signer.public_key(), algorithms=["RS256"])
                assert claims["iss"] in (CLIENT_ID, APP_ID)
                state.requests.append((self.command, path, "app"))
                if path == "/app":
                    return self.reply(200, {"slug": "astrolift-conflict", "name": "astrolift-conflict"})
                if path == "/app/installations":
                    return self.reply(
                        200, [{"id": int(i), "account": {"login": login}} for i, login in ACCOUNTS.items()]
                    )
                installation = path.removeprefix("/app/installations/").removesuffix("/access_tokens")
                if self.command == "POST" and installation in INSTALLED_REPOS:
                    expires = (timezone.now() + timedelta(hours=1)).isoformat()
                    return self.reply(201, {"token": f"token-{installation}", "expires_at": expires})
                return self.reply(404)

            installation = auth.partition(" ")[2].removeprefix("token-")
            state.requests.append((self.command, path, installation))
            if installation not in INSTALLED_REPOS:
                return self.reply(401)
            visible = {name.lower() for name in INSTALLED_REPOS[installation]}
            if path == "/installation/repositories":
                repos = [{"full_name": name} for name in INSTALLED_REPOS[installation]]
                return self.reply(200, {"total_count": len(repos), "repositories": repos})
            parts = path.split("/", 4)
            if len(parts) < 5 or parts[1] != "repos" or f"{parts[2]}/{parts[3]}".lower() not in visible:
                return self.reply(404, {"message": "Not Found"})
            route = parts[4]
            if route == "actions/secrets/public-key":
                return self.reply(
                    200, {"key": base64.b64encode(bytes(sealing.public_key)).decode(), "key_id": "k"}
                )
            if route.startswith("actions/secrets/") and self.command == "PUT":
                state.secrets[route.removeprefix("actions/secrets/")] = body["encrypted_value"]
                return self.reply(201)
            if route == "branches/main":
                return self.reply(200, {"name": "main", "protected": False, "commit": {"sha": "b" * 40}})
            if route.startswith("contents/"):
                file = route.removeprefix("contents/")
                if self.command == "PUT":
                    state.files[file] = base64.b64decode(body["content"]).decode()
                    return self.reply(201, {"commit": {"sha": "a" * 40}, "content": {"path": file}})
                if file in state.files:
                    return self.reply(200, raw=state.files[file])
            return self.reply(404, {"message": "Not Found"})

        do_GET = handle_request
        do_PUT = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    state.pem = signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def org(settings):
    settings.PLATFORM_API_URL = "https://platform.example.test"
    settings.APP_BASE_URL = "https://platform.example.test"
    return Organization.objects.create(name="CONFLICT", slug="conflict-2576")


@pytest.fixture
def app(org):
    team = Team.objects.create(organization=org, name="Design", slug="design-2576")
    project = Project.objects.create(organization=org, team=team, name="Design", slug="design-2576")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="cykick-storybook",
        slug="cykick-storybook",
        source_kind="github",
        source_repo=REPO,
        registry_repo_uri="123456789012.dkr.ecr.us-west-2.amazonaws.com/cykick-storybook",
        push_role_ref="arn:aws:iam::123456789012:role/ci-build",
    )


def _installation(org, github, installation_id, *, is_active=True, app_name="astrolift-conflict"):
    pem = encrypt_at_rest(github.pem)
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name=f"GitHub App: {app_name}",
        account_login={**ACCOUNTS, OTHER_APP_ON_CALLIOPEAI: "calliopeai"}[installation_id],
        installation_id=installation_id,
        oauth_client_id=APP_ID,
        app_client_id=CLIENT_ID,
        api_base_url=github.url,
        secret_backend_kind=pem.backend_kind,
        secret_ciphertext=pem.backend_ref,
        is_active=is_active,
    )


def _info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False, pk=None, username=""))
    return SimpleNamespace(context=SimpleNamespace(request=request))


def _ci_setup(org, app):
    """What ``astro ci setup`` runs: webhook, then secrets, then workflow."""
    mutations = LifecycleMutation()
    with tenant_context(TenantContext(organization_id=org.pk)):
        return (
            mutations.install_astrolift_source_webhook(
                _info(), input=InstallSourceWebhookInput(app_slug=app.slug)
            ),
            mutations.push_astrolift_ci_secrets_to_repo(
                _info(), input=PushCiSecretsToRepoInput(app_slug=app.slug)
            ),
            mutations.push_astrolift_ci_workflow_to_repo(
                _info(), input=PushCiWorkflowToRepoInput(app_slug=app.slug)
            ),
        )


def test_ci_setup_uses_the_installation_on_the_repository_owner(org, app, github, permission_resolver):
    """ConflictHQ is the older installation, which ranking used to fall back
    to; every step of CI setup for calliopeai/cykick runs as calliopeai's."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _installation(org, github, CONFLICTHQ)
    _installation(org, github, CALLIOPEAI)

    webhook, secrets, workflow = _ci_setup(org, app)

    assert webhook.ok, webhook.errors
    assert webhook.data.status == "app_delivers"
    assert secrets.ok, secrets.errors
    assert len(secrets.data.secret_names) == 5
    assert set(github.secrets) == set(secrets.data.secret_names)
    assert workflow.ok, workflow.errors
    assert workflow.data.status == "created"
    assert github_workflow_path_for(app) in github.files

    installations = {installation for _, _, installation in github.requests}
    assert installations == {"app", CALLIOPEAI}
    assert ("POST", f"/app/installations/{CONFLICTHQ}/access_tokens", "app") not in github.requests
    assert all(
        path.startswith(f"/repos/{REPO}/") for _, path, _ in github.requests if path.startswith("/repos/")
    )


def test_an_inactive_owner_connection_is_named_and_never_replaced(org, app, github, permission_resolver):
    """calliopeai's connection exists but is inactive. Each step says so for
    calliopeai, without calling the App uninstalled, and nothing runs as
    ConflictHQ's installation."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _installation(org, github, CONFLICTHQ)
    _installation(org, github, CALLIOPEAI, is_active=False)

    results = _ci_setup(org, app)

    for result in results:
        assert not result.ok
        assert result.errors[0].code == "PRECONDITION"
        message = result.errors[0].message
        assert "no active GitHub App installation for calliopeai" in message
        assert "connection for calliopeai (GitHub App: astrolift-conflict) is inactive" in message
        assert "with calliopeai as the GitHub org / user login" in message
        assert "not installed" not in message
        assert "ConflictHQ" not in message
    assert github.requests == []
    app.refresh_from_db()
    assert app.source_webhook_installed_at is None
    assert app.ci_secrets_pushed_at is None


def test_an_owner_installation_without_the_repo_is_named(org, app, github, permission_resolver):
    """The live shape from #2577: calliopeai's active connection is another
    App, whose installation does not include the repo. The webhook step, the
    one ``astro ci setup`` stops at, names that App rather than "the GitHub
    App", and the ConflictHQ installation is never asked."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _installation(org, github, CONFLICTHQ)
    _installation(org, github, OTHER_APP_ON_CALLIOPEAI, app_name="astrolift-ai")

    webhook, _, _ = _ci_setup(org, app)

    assert not webhook.ok
    assert webhook.errors[0].message.startswith(
        "GitHub App: astrolift-ai is not installed on calliopeai/cykick;"
    )
    assert {installation for _, _, installation in github.requests} <= {"app", OTHER_APP_ON_CALLIOPEAI}
    assert ("POST", f"/app/installations/{CONFLICTHQ}/access_tokens", "app") not in github.requests


def test_a_missing_owner_connection_is_named_for_every_ci_identity(org, app, github):
    """With no calliopeai connection at all, the error says it is missing
    and how to add it; no picker, App-only or not, returns ConflictHQ."""
    _installation(org, github, CONFLICTHQ)
    SourceConnection.objects.create(
        organization=org, kind=SourceConnection.Kind.GITHUB_PAT, account_login="ci-bot", is_active=True
    )

    with pytest.raises(ConnectionResolutionError) as refused:
        resolve_connection(org, purpose=PLATFORM_REPO_WRITE, repo=REPO)
    assert refused.value.code == "PRECONDITION"
    assert refused.value.message == (
        "no active GitHub App installation for calliopeai: this organization has no connection for "
        "calliopeai. Add one under Providers → Source → Connect to GitHub → Connect an existing GitHub "
        "App, with calliopeai as the GitHub org / user login."
    )
    # A token connection can still reach what its account reaches; another
    # account's installation never can.
    for picked in (
        resolve_connection(org, purpose=ORG_REPO_WRITE, repo=REPO),
        webhooks._pick_source_connection(app),
        manifest_sync._pick_source_connection(app),
        workflow_sync._pick_source_connection(app),
    ):
        assert picked.kind == SourceConnection.Kind.GITHUB_PAT


def test_an_org_whose_default_is_conflicthq_adopts_and_uses_calliopeai(org, app, github, permission_resolver):
    """Leo's case: the organization's first, default connection is
    ConflictHQ. Connecting the App again without naming an account used to
    re-adopt that default; now the operator names calliopeai, the installation
    adopted is calliopeai's, and every CI identity for calliopeai/cykick is
    that connection."""
    permission_resolver.grant(Permission.SCM_CONNECT)
    default = _installation(org, github, CONFLICTHQ)

    def adopt(org_login):
        with tenant_context(TenantContext(organization_id=org.pk)):
            return ScmMutation().connect_existing_github_app(
                _info(),
                input=ConnectExistingGithubAppInput(
                    app_id=APP_ID,
                    private_key_pem=github.pem.decode(),
                    client_id=CLIENT_ID,
                    api_base_url=github.url,
                    org_login=org_login,
                ),
            )

    unnamed = adopt(None)
    assert not unnamed.ok
    assert (unnamed.errors[0].code, unnamed.errors[0].field) == ("VALIDATION", "orgLogin")
    assert "ConflictHQ, calliopeai" in unnamed.errors[0].message
    assert not any(path.endswith("/access_tokens") for _, path, _ in github.requests)
    assert list(SourceConnection.objects.filter(organization=org).values_list("pk", flat=True)) == [
        default.pk
    ]

    named = adopt("calliopeai")
    assert named.ok, named.errors
    added = SourceConnection.objects.get(guid=named.data.id)
    assert (added.account_login, added.installation_id) == ("calliopeai", CALLIOPEAI)
    assert ("POST", f"/app/installations/{CALLIOPEAI}/access_tokens", "app") in github.requests
    assert ("POST", f"/app/installations/{CONFLICTHQ}/access_tokens", "app") not in github.requests
    default.refresh_from_db()
    assert (default.account_login, default.installation_id) == ("ConflictHQ", CONFLICTHQ)

    for purpose in (PLATFORM_REPO_WRITE, ORG_REPO_WRITE):
        assert resolve_connection(org, purpose=purpose, repo=app.source_repo).pk == added.pk
    for picker in (webhooks, manifest_sync, workflow_sync):
        assert picker._pick_source_connection(app).pk == added.pk
