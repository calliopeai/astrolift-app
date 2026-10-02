"""Real PostgreSQL, encrypted credentials and HTTP GitHub protocol boundaries.

The owned receiver implements only the exercised GitHub API contract. These
cases do not invoke GitHub Actions, Docker, AWS or production deployments.
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
from nacl.public import PrivateKey, SealedBox

from astrolift_lifecycle.deploy_tokens import verify_token
from astrolift_registry.models import Workload
from astrolift_scm.ci_identity import github_ci_secret_name
from astrolift_scm.ci_templates import git_blob_sha
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers.github import GithubProviderError, delete_github_file
from astrolift_scm.services.ci_workflow_drift import fetch_repo_ci_workflow
from astrolift_scm.services.secrets import push_astrolift_ci_secrets, validate_astrolift_ci_secrets
from astrolift_scm.services.workflow_sync import (
    _remove_superseded_workflow,
    github_workflow_path_for,
    render_astrolift_ci_workflow,
    sync_workflow_file_to_repo,
)
from astrolift_scm.services.workflows import dispatch_astrolift_ci_workflow
from astrolift_scm.tests.test_ci_monorepo_2139 import apps as registered_apps
from core.secrets import encrypt_at_rest

apps = registered_apps
pytestmark = pytest.mark.django_db


@pytest.fixture
def host(apps):
    key = PrivateKey.generate()
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    state = SimpleNamespace(files={}, secrets={}, dispatches=[], deletes=[], requests=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def response(self, status, body=None):
            payload = body.encode() if isinstance(body, str) else json.dumps(body or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if status != 204:
                self.wfile.write(payload)

        def handle_request(self):
            path = unquote(urlsplit(self.path).path)
            state.requests.append((self.command, path))
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if path == "/app/installations/2139/access_tokens":
                jwt.decode(
                    self.headers["Authorization"].removeprefix("Bearer "),
                    signer.public_key(),
                    algorithms=["RS256"],
                    issuer="ci-2139-client",
                )
                return self.response(
                    201,
                    {
                        "token": "owned-installation-token",
                        "expires_at": (timezone.now() + timedelta(hours=1)).isoformat(),
                    },
                )
            if self.headers.get("Authorization") not in {
                "Bearer owned-installation-token",
                "token owned-installation-token",
            }:
                return self.response(401)
            prefix = "/repos/example/monorepo/"
            if not path.startswith(prefix):
                return self.response(404)
            route = path.removeprefix(prefix)
            if route == "actions/secrets/public-key":
                return self.response(
                    200, {"key": base64.b64encode(bytes(key.public_key)).decode(), "key_id": "owned"}
                )
            if route == "actions/secrets":
                return self.response(
                    200,
                    {
                        "total_count": len(state.secrets),
                        "secrets": [
                            {"name": name, "updated_at": timezone.now().isoformat()} for name in state.secrets
                        ],
                    },
                )
            if route.startswith("actions/secrets/") and self.command == "PUT":
                assert body["key_id"] == "owned"
                state.secrets[route.removeprefix("actions/secrets/")] = body["encrypted_value"]
                return self.response(204)
            if route == "branches/main/protection":
                return self.response(404)
            if route.startswith("contents/"):
                file = route.removeprefix("contents/")
                current = state.files.get(file)
                if self.command == "GET":
                    if current is None:
                        return self.response(404)
                    if "raw" in self.headers.get("Accept", ""):
                        return self.response(200, current)
                    return self.response(
                        200,
                        {
                            "type": "file",
                            "encoding": "base64",
                            "content": base64.b64encode(current.encode()).decode(),
                            "sha": git_blob_sha(current.encode()),
                        },
                    )
                if self.command == "PUT":
                    assert body["branch"] == "main"
                    if current is not None and body.get("sha") != git_blob_sha(current.encode()):
                        return self.response(409)
                    state.files[file] = base64.b64decode(body["content"]).decode()
                    return self.response(201, {"commit": {"sha": "a" * 40}, "content": {"path": file}})
                if self.command == "DELETE":
                    if current is None:
                        return self.response(404)
                    if body.get("sha") != git_blob_sha(current.encode()):
                        return self.response(409)
                    state.deletes.append(file)
                    del state.files[file]
                    return self.response(200)
            if route.endswith("/dispatches"):
                state.dispatches.append((route, body))
                return self.response(204)
            return self.response(404)

        do_GET = handle_request
        do_PUT = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pem = signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    encrypted = encrypt_at_rest(pem)
    state.connection = SourceConnection.objects.create(
        organization=apps[0].organization,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        account_login="example",
        app_client_id="ci-2139-client",
        installation_id="2139",
        api_base_url=f"http://127.0.0.1:{server.server_port}",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
    )
    state.plaintext = lambda name: SealedBox(key).decrypt(base64.b64decode(state.secrets[name])).decode()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_two_app_secret_push_rotates_only_selected_app_and_validates_own_names(apps, host):
    first, second = apps
    for app in apps:
        result = push_astrolift_ci_secrets(
            app, viewer_user=None, platform_api_url="https://platform.example.test/"
        )
        assert result.ok
        assert len(result.secret_names) == 5
        assert set(result.secret_names) == {
            github_ci_secret_name(app, name)
            for name in (
                "ASTROLIFT_PUSH_ROLE_ARN",
                "ASTROLIFT_ECR_URI",
                "ASTROLIFT_APP_SLUG",
                "ASTROLIFT_API_URL",
                "ASTROLIFT_DEPLOY_TOKEN",
            )
        }
        assert host.plaintext(github_ci_secret_name(app, "ASTROLIFT_APP_SLUG")) == app.slug
        assert host.plaintext(github_ci_secret_name(app, "ASTROLIFT_ECR_URI")) == app.registry_repo_uri
    assert len(host.secrets) == 10
    sibling = {name: value for name, value in host.secrets.items() if name.endswith(second.guid.hex.upper())}
    first_token = host.plaintext(github_ci_secret_name(first, "ASTROLIFT_DEPLOY_TOKEN"))
    second_token = host.plaintext(github_ci_secret_name(second, "ASTROLIFT_DEPLOY_TOKEN"))
    assert verify_token(first_token).registered_app_id == first.pk
    assert verify_token(second_token).registered_app_id == second.pk
    rotated = push_astrolift_ci_secrets(
        first, viewer_user=None, platform_api_url="https://platform.example.test"
    )
    assert rotated.ok
    assert verify_token(first_token) is None
    assert verify_token(second_token).registered_app_id == second.pk
    assert all(host.secrets[name] == value for name, value in sibling.items())
    checks = validate_astrolift_ci_secrets(first, viewer_user=None)
    assert checks.ok and all(item.is_set and item.is_current for item in checks.results)
    del host.secrets[github_ci_secret_name(first, "ASTROLIFT_DEPLOY_TOKEN")]
    checks = validate_astrolift_ci_secrets(first, viewer_user=None)
    assert any(not item.is_set for item in checks.results)
    assert all(item.is_set for item in validate_astrolift_ci_secrets(second, viewer_user=None).results)


def test_two_app_real_provider_sync_drift_read_and_default_dispatch_keep_targets(apps, host):
    for app in apps:
        assert sync_workflow_file_to_repo(app).status == "created"
        assert app.ci_workflow_state["path"] == github_workflow_path_for(app)
        assert fetch_repo_ci_workflow(app) == render_astrolift_ci_workflow(app)
        assert sync_workflow_file_to_repo(app).status == "in_sync"
        assert dispatch_astrolift_ci_workflow(app).ok
    assert set(host.files) == {github_workflow_path_for(app) for app in apps}
    assert [route for route, _ in host.dispatches] == [
        "actions/workflows/" + github_workflow_path_for(app).rsplit("/", 1)[-1] + "/dispatches"
        for app in apps
    ]
    assert all(body == {"ref": "main"} for _, body in host.dispatches)


def test_clean_owned_counterpart_is_removed_but_edited_and_legacy_files_survive(apps, host):
    app = apps[0]
    current = render_astrolift_ci_workflow(app)
    ordinary = github_workflow_path_for(app)
    host.files[ordinary] = current
    Workload.objects.create(registered_app=app, name="Agent", slug="agent", kind=Workload.Kind.AGENT)
    assert _remove_superseded_workflow(host.connection, app, branch="main") == ordinary
    assert host.deletes == [ordinary]
    for retained in (
        current + "# operator edit\n",
        "# astrolift-managed: template-version=7 sha256=" + "0" * 16 + "\nname: legacy\n",
        render_astrolift_ci_workflow(apps[1]),
    ):
        host.files[ordinary] = retained
        assert _remove_superseded_workflow(host.connection, app, branch="main") is None
        assert host.files[ordinary] == retained
    assert host.deletes == [ordinary]


def test_conditional_delete_refuses_content_changed_after_review_without_refetch(apps, host):
    path = github_workflow_path_for(apps[0])
    reviewed = render_astrolift_ci_workflow(apps[0])
    host.files[path] = reviewed + "# concurrent operator edit\n"
    before = len(host.requests)
    with pytest.raises(GithubProviderError, match="HTTP 409"):
        delete_github_file(
            host.connection,
            repo_full_name=apps[0].source_repo,
            path=path,
            branch="main",
            commit_message="remove reviewed file",
            expected_sha=git_blob_sha(reviewed.encode()),
        )
    assert [request for request in host.requests[before:] if request[1].startswith("/repos/")] == [
        ("DELETE", "/repos/example/monorepo/contents/" + path)
    ]
    assert host.files[path].endswith("# concurrent operator edit\n")


@pytest.mark.parametrize("field", ["registry_repo_uri", "dockerfile_path", "build_context"])
def test_real_bearer_api_refuses_missing_build_inputs_before_repo_writes(apps, host, settings, field):
    from django.contrib.auth import get_user_model
    from django.test import Client

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken, Member, Role, RoleBinding
    from core.permissions import Permission

    app = apps[0]
    setattr(app, field, "")
    app.save(update_fields=[field])
    user = get_user_model().objects.create_user(username="ci-monorepo-setup")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=app.organization_id)
    role = Role.objects.create(
        organization=app.organization,
        name="CI setup",
        slug="ci-setup",
        scope_level="ORG",
        permissions=[Permission.APP_UPDATE.value],
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=app.organization_id)
    query = (
        "mutation($input:PushCiWorkflowInput!){pushCiWorkflow(input:$input){ok errors{code message field}}}"
    )
    for scope in ("read:apps", "write:apps"):
        issued = mint_token()
        ApiToken.objects.create(
            user=user,
            organization=app.organization,
            name="CI setup",
            token_hash=issued.token_hash,
            scopes=[scope],
        )
        reply = Client().post(
            f"/{settings.BASE_URL}gql/config/",
            data={
                "query": query,
                "variables": {"input": {"appId": str(app.guid), "connectionId": str(host.connection.guid)}},
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {issued.plaintext}",
            HTTP_X_ASTROLIFT_ORGANIZATION=str(app.organization.guid),
        )
        assert reply.status_code == 200
        result = reply.json()
        assert not result.get("errors"), result
        envelope = result["data"]["pushCiWorkflow"]
        if scope == "read:apps":
            assert not envelope["ok"]
            assert envelope["errors"][0]["code"] == "PERMISSION_DENIED"
        else:
            assert not envelope["ok"]
            assert envelope["errors"][0]["code"] == "PRECONDITION"
            assert "ci_pushed requires" in envelope["errors"][0]["message"]
        assert not host.requests and not host.files


@pytest.mark.parametrize("changed", ["organization_id", "source_repo"])
def test_intact_stamp_with_changed_owner_context_never_deletes(apps, host, changed):
    from astrolift_scm.ci_identity import github_ci_owner
    from astrolift_scm.ci_templates import TEMPLATE_VERSION, content_hash, parse_stamp, stamp_workflow

    app = apps[0]
    current = render_astrolift_ci_workflow(app)
    path = github_workflow_path_for(app)
    owner = github_ci_owner(app)
    owner[changed] = app.organization_id + 1 if changed == "organization_id" else "other/monorepo"
    lines = parse_stamp(current).body_without_stamp.splitlines(keepends=True)
    lines[0] = "# astrolift-ci-owner: " + json.dumps(owner) + "\n"
    body = "".join(lines)
    host.files[path] = stamp_workflow(body, version=TEMPLATE_VERSION, digest=content_hash(body))
    Workload.objects.create(registered_app=app, name="Agent", slug="agent", kind=Workload.Kind.AGENT)
    assert _remove_superseded_workflow(host.connection, app, branch="main") is None
    assert not host.deletes and path in host.files
