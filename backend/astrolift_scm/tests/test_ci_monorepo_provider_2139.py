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
from urllib.parse import parse_qs, unquote, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone
from nacl.public import PrivateKey, SealedBox

from astrolift_lifecycle.deploy_tokens import verify_token
from astrolift_registry.models import Workload
from astrolift_scm.ci_identity import github_ci_identity, github_ci_secret_name
from astrolift_scm.ci_templates import git_blob_sha
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError, put_file
from astrolift_scm.providers.github import GithubProviderError, delete_github_file, put_github_file
from astrolift_scm.services.ci_workflow_drift import fetch_repo_ci_workflow
from astrolift_scm.services.secrets import push_astrolift_ci_secrets, validate_astrolift_ci_secrets
from astrolift_scm.services.workflow_sync import (
    _remove_superseded_workflow,
    _side_branch_for,
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
    state = SimpleNamespace(
        files={},
        secrets={},
        dispatches=[],
        deletes=[],
        requests=[],
        branches={},
        protected=False,
        pulls=[],
        after_read=None,
    )
    state.branches["main"] = state.files

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
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
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
                return self.response(200 if state.protected else 404)
            if route == "branches/main":
                return self.response(200, {"commit": {"sha": "b" * 40}})
            if route == "git/refs" and self.command == "POST":
                branch = body["ref"].removeprefix("refs/heads/")
                assert body["sha"] == "b" * 40
                if branch in state.branches:
                    return self.response(422, {"message": "Reference already exists"})
                state.branches[branch] = dict(state.files)
                return self.response(201)
            if route == "pulls" and self.command == "POST":
                state.pulls.append(body)
                return self.response(201, {"html_url": "https://github.example.test/example/monorepo/pull/1"})
            if route.startswith("contents/"):
                file = route.removeprefix("contents/")
                branch = (
                    parse_qs(parsed.query).get("ref", ["main"])[0]
                    if self.command == "GET"
                    else body["branch"]
                )
                if branch not in state.branches:
                    return self.response(404)
                files = state.branches[branch]
                current = files.get(file)
                if self.command == "GET":
                    if state.after_read:
                        state.after_read(branch, file, current)
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
                    if current is not None and body.get("sha") != git_blob_sha(current.encode()):
                        return self.response(409)
                    if current is None and "sha" in body:
                        return self.response(422)
                    files[file] = base64.b64decode(body["content"]).decode()
                    return self.response(201, {"commit": {"sha": "a" * 40}, "content": {"path": file}})
                if self.command == "DELETE":
                    if current is None:
                        return self.response(404)
                    if body.get("sha") != git_blob_sha(current.encode()):
                        return self.response(409)
                    state.deletes.append(file)
                    del files[file]
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


@pytest.mark.parametrize("initial", ["absent", "existing", "deleted"])
def test_managed_put_refuses_concurrent_edit_creation_or_deletion_without_new_baseline(apps, host, initial):
    app = apps[0]
    path = github_workflow_path_for(app)
    if initial != "absent":
        assert sync_workflow_file_to_repo(app).status == "created"
        app.build_args = {"VERSION": "next"}
        app.save(update_fields=["build_args"])
    receipt = dict(app.ci_workflow_state)
    version = app.ci_workflow_template_version
    concurrent = "# operator-owned content\nname: independent\n"

    def change_after_review(branch, file, _current):
        if branch == "main" and file == path:
            host.after_read = None
            if initial == "deleted":
                del host.files[path]
            else:
                host.files[path] = concurrent

    host.after_read = change_after_review
    before = len(host.requests)
    result = sync_workflow_file_to_repo(app)
    assert result.status == "fetch_failed" and result.error.startswith("CONFLICT:")
    assert concurrent not in result.error
    assert host.files.get(path) == (None if initial == "deleted" else concurrent)
    assert not host.pulls and not host.deletes
    assert (
        sum(
            method == "GET" and route.endswith("/contents/" + path)
            for method, route in host.requests[before:]
        )
        == 1
    )
    app.refresh_from_db()
    assert app.ci_workflow_state == receipt and app.ci_workflow_template_version == version


@pytest.mark.parametrize("forced", [False, True])
@pytest.mark.parametrize("initial", ["absent", "existing", "deleted"])
def test_pr_target_put_is_conditional_after_its_own_review(apps, host, forced, initial):
    app = apps[0]
    path = github_workflow_path_for(app)
    if initial != "absent":
        assert sync_workflow_file_to_repo(app).status == "created"
        app.build_args = {"VERSION": "next"}
        app.save(update_fields=["build_args"])
    receipt = dict(app.ci_workflow_state)
    host.protected = not forced
    side_branch = _side_branch_for(github_ci_identity(app))
    concurrent = "# edited review branch\nname: operator proposal\n"

    def change_after_review(branch, file, _current):
        if branch == side_branch and file == path:
            host.after_read = None
            if initial == "deleted":
                del host.branches[branch][path]
            else:
                host.branches[branch][path] = concurrent

    host.after_read = change_after_review
    before = len(host.requests)
    result = sync_workflow_file_to_repo(app, force_pr=forced)
    assert result.status == "fetch_failed" and result.error.startswith("CONFLICT:")
    assert host.branches[side_branch].get(path) == (None if initial == "deleted" else concurrent)
    assert not host.pulls
    assert (
        sum(
            method == "GET" and route.endswith("/contents/" + path)
            for method, route in host.requests[before:]
        )
        == 2
    )
    app.refresh_from_db()
    assert app.ci_workflow_state == receipt


@pytest.mark.parametrize("side_kind", ["deleted", "edited", "foreign_owner"])
def test_existing_review_branch_operator_edit_or_deletion_is_never_overwritten(apps, host, side_kind):
    app = apps[0]
    assert sync_workflow_file_to_repo(app).status == "created"
    path = github_workflow_path_for(app)
    original = host.files[path]
    side_content = {
        "deleted": None,
        "edited": "# independent PR edit\n",
        "foreign_owner": render_astrolift_ci_workflow(apps[1]),
    }[side_kind]
    side_branch = _side_branch_for(github_ci_identity(app))
    host.branches[side_branch] = {} if side_content is None else {path: side_content}
    app.build_args = {"VERSION": "next"}
    app.save(update_fields=["build_args"])
    before = len(host.requests)
    result = sync_workflow_file_to_repo(app, force_pr=True)
    assert result.status == "fetch_failed" and result.error.startswith("CONFLICT:")
    assert host.files[path] == original and host.branches[side_branch].get(path) == side_content
    assert not host.pulls
    assert not any(method == "PUT" for method, _ in host.requests[before:])


def test_pr_branch_created_from_concurrently_edited_base_refuses_unreviewed_content(apps, host):
    app = apps[0]
    path = github_workflow_path_for(app)
    concurrent = "# concurrent base edit\n"

    def change_after_review(branch, file, _current):
        if branch == "main" and file == path:
            host.after_read = None
            host.files[path] = concurrent

    host.after_read = change_after_review
    result = sync_workflow_file_to_repo(app, force_pr=True)
    side_branch = _side_branch_for(github_ci_identity(app))
    assert result.status == "fetch_failed" and result.error.startswith("CONFLICT:")
    assert host.files[path] == host.branches[side_branch][path] == concurrent
    assert not host.pulls and not any(method == "PUT" for method, _ in host.requests)


def test_deleted_existing_proposal_is_not_recreated_when_base_is_also_absent(apps, host):
    app = apps[0]
    side_branch = _side_branch_for(github_ci_identity(app))
    assert sync_workflow_file_to_repo(app, force_pr=True).status == "pr_opened"
    host.branches[side_branch].clear()
    host.pulls.clear()
    before = len(host.requests)
    result = sync_workflow_file_to_repo(app, force_pr=True)
    assert result.status == "fetch_failed" and result.error.startswith("CONFLICT:")
    assert not host.files and not host.branches[side_branch] and not host.pulls
    assert not any(method == "PUT" for method, _ in host.requests[before:])


def test_unchanged_owned_review_branch_can_refresh_using_its_reviewed_blob(apps, host):
    app = apps[0]
    path = github_workflow_path_for(app)
    for value in ("first", "next"):
        app.build_args = {"VERSION": value}
        app.save(update_fields=["build_args"])
        assert sync_workflow_file_to_repo(app, force_pr=True).status == "pr_opened"
        side_branch = _side_branch_for(github_ci_identity(app))
        assert host.branches[side_branch][path] == render_astrolift_ci_workflow(app)
        assert path not in host.files


def test_operator_drift_gets_reviewable_conditional_proposal_without_changing_base(apps, host):
    app = apps[0]
    path = github_workflow_path_for(app)
    assert sync_workflow_file_to_repo(app).status == "created"
    edited = host.files[path] + "# keep operator edits on main\n"
    host.files[path] = edited
    assert sync_workflow_file_to_repo(app).status == "pr_opened"
    side_branch = _side_branch_for(github_ci_identity(app))
    assert host.files[path] == edited
    assert host.branches[side_branch][path] == render_astrolift_ci_workflow(app)
    assert host.pulls[0]["head"] == side_branch and host.pulls[0]["base"] == "main"


def test_put_without_condition_retains_legacy_lookup_and_update_behavior(apps, host):
    path = github_workflow_path_for(apps[0])
    host.files[path] = "# legacy content\n"
    result = put_file(
        host.connection,
        repo_full_name=apps[0].source_repo,
        path=path,
        branch="main",
        content="# updated\n",
        commit_message="legacy update",
    )
    assert result.commit_sha == "a" * 40 and host.files[path] == "# updated\n"
    assert [method for method, route in host.requests if route.endswith("/contents/" + path)] == [
        "GET",
        "PUT",
    ]


@pytest.mark.parametrize("kind", ["gitlab_pat", "bitbucket_oauth_user", "gitea_pat", "unknown"])
@pytest.mark.parametrize("condition", [{"expected_sha": "a" * 40}, {"expected_absent": True}])
def test_unsupported_conditional_provider_refuses_before_network(apps, host, kind, condition):
    with pytest.raises(ProviderError, match="conditional file writes") as error:
        put_file(
            SimpleNamespace(kind=kind),
            repo_full_name=apps[0].source_repo,
            path=github_workflow_path_for(apps[0]),
            branch="main",
            content="new",
            commit_message="conditional update",
            **condition,
        )
    assert error.value.code == "UNSUPPORTED" and not host.requests


@pytest.mark.parametrize(
    "condition",
    [
        {"expected_sha": "bad"},
        {"expected_sha": "A" * 40},
        {"expected_sha": "a" * 40, "expected_absent": True},
    ],
)
def test_invalid_conditional_write_refuses_before_credential_or_http(apps, host, condition):
    with pytest.raises(ValueError):
        put_github_file(
            SimpleNamespace(kind="github_pat"),
            repo_full_name=apps[0].source_repo,
            path=github_workflow_path_for(apps[0]),
            branch="main",
            content="new",
            commit_message="conditional update",
            **condition,
        )
    assert not host.requests


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
