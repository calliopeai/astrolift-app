"""Tests for the Calliope App Builder REST surface (#767, #768).

Covers the three endpoints:

* ``POST   /api/builder/v1/dev-environments/``
* ``PUT    /api/builder/v1/dev-environments/<guid>/files/``
* ``POST   /api/builder/v1/dev-environments/<guid>/promote/``

Auth flows through the existing :class:`ApiTokenAuthMiddleware`; we
mint a real ApiToken row + Bearer header rather than stubbing
``request.user`` so the tests exercise the integration. Temporal is
patched at ``astrolift_workflows.client.start_workflow`` so tests don't
need a running Temporal server.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid

import pytest
from _sdk.cluster import StorageClassInfo
from constance.test import override_config
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.api_tokens import PLAINTEXT_PREFIX
from astrolift_identity.models import (
    ApiToken,
    Member,
    Organization,
    OrganizationModule,
    Role,
    RoleBinding,
    Team,
)
from astrolift_lifecycle.models import AppEnvironment, DevEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.dev_environment import _deploy_promoted_app_sync
from astrolift_workflows.tests.test_builder_runtime_1858 import (
    _by_kind,
    _RecordingDriver,
    _seed,
    _sqlite_bytes,
)
from core.permissions import Permission

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---- fixtures --------------------------------------------------------


@pytest.fixture
def org():
    org = Organization.objects.create(name="Builder Org", slug="builder-org")
    # The builder API requires the org's chat_studio_integration module (#1859).
    OrganizationModule.objects.create(
        organization=org, key=OrganizationModule.Key.CHAT_STUDIO_INTEGRATION, enabled=True
    )
    return org


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other Org", slug="other-org")


@pytest.fixture
def team(org):
    return Team.objects.create(organization=org, name="Eng", slug="eng")


@pytest.fixture
def user(org, team):
    u = User.objects.create_user(username="builder-user", email="builder@astrolift.dev", password="pw")
    Member.objects.create(
        user=u,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    # The routes check app.create and app.deploy (#1878). These tests cover
    # what an allowed caller gets; test_builder_authz_1872_1878.py covers
    # who is allowed.
    role = Role.objects.create(
        organization=org,
        name="Builder",
        slug="builder",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_CREATE.value, Permission.APP_DEPLOY.value],
    )
    RoleBinding.objects.create(user=u, role=role, scope_kind=RoleBinding.ScopeKind.ORG, scope_id=org.id)
    # Also a TEAM-scope binding on ``team`` (#1919): create now resolves
    # ``team_slug`` from the caller's own single team when none is passed,
    # and every create call in this module relies on that fallback rather
    # than naming ``team_slug`` itself.
    RoleBinding.objects.create(user=u, role=role, scope_kind=RoleBinding.ScopeKind.TEAM, scope_id=team.id)
    return u


@pytest.fixture
def provider_plugin():
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s",
                slug="k8s-builder",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    return ProviderPlugin.objects.get(slug="k8s-builder")


@pytest.fixture
def cluster(org, provider_plugin):
    return TenantCluster.objects.create(
        organization=org,
        name="builder-cluster",
        slug="builder-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://builder-cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        is_active=True,
    )


@pytest.fixture
def api_token(user, org):
    """Mint a real ApiToken row and return ``(row, plaintext_bearer)``."""
    plaintext = PLAINTEXT_PREFIX + uuid.uuid4().hex
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    row = ApiToken.objects.create(
        user=user,
        organization=org,
        name="builder-test",
        token_hash=digest,
        token_last_4=plaintext[-4:],
        scopes=["admin"],
    )
    return row, plaintext


@pytest.fixture
def auth_headers(api_token):
    _, plaintext = api_token
    return {"HTTP_AUTHORIZATION": f"Bearer {plaintext}"}


@pytest.fixture
def workflow_starts(monkeypatch):
    """Capture every ``start_workflow`` call so assertions can inspect them."""
    starts: list[dict] = []

    def _fake(name, args, *, workflow_id, task_queue=None):
        starts.append(
            {
                "name": name,
                "args": list(args),
                "workflow_id": workflow_id,
                "task_queue": task_queue,
            }
        )
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(
            workflow_id=workflow_id,
            run_id=f"run-{len(starts)}",
            enqueued=True,
        )

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _fake)
    return starts


# ---- helpers ---------------------------------------------------------


def _post_json(client, path, payload, headers):
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _put_json(client, path, payload, headers):
    return client.put(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


# ---- create_dev_environment -----------------------------------------


def test_create_happy_path_returns_creating_status_and_enqueues_workflow(
    cluster, team, auth_headers, workflow_starts
):
    client = Client()
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {
            "runtime": "python",
            "runtime_version": "3.12",
            "start_command": "python main.py",
            "port": 8000,
            "env_vars": {"DEBUG": "1"},
            "resource_profile": "small",
        },
        auth_headers,
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "creating"
    assert body["preview_url"] is None
    assert body["id"]

    dev = DevEnvironment.objects.get(guid=body["id"])
    assert dev.runtime == "python"
    assert dev.runtime_version == "3.12"
    assert dev.port == 8000
    assert dev.env_vars == {"DEBUG": "1"}
    assert dev.tenant_cluster_id == cluster.id

    assert len(workflow_starts) == 1
    start = workflow_starts[0]
    assert start["name"] == "CreateDevEnvironmentWorkflow"
    assert start["workflow_id"] == f"CreateDevEnvironmentWorkflow-{dev.guid}"
    assert start["task_queue"] == "astrolift-main"


def test_create_without_auth_returns_401(cluster, workflow_starts):
    client = Client()
    r = _post_json(client, "/api/builder/v1/dev-environments/", {}, {})
    assert r.status_code == 401
    assert workflow_starts == []


@pytest.mark.parametrize("header", ["other-org", "malformed"])
def test_create_with_a_selected_organization_that_disagrees_with_the_token_returns_403(
    cluster, other_org, auth_headers, workflow_starts, header
):
    """``auth_headers`` carries a token issued to ``org`` (via ``cluster``).
    Every other test in this module either omits the selected-organization
    header or matches it, so none of them exercise the reject path
    ``TenantContextMiddleware`` owns: a conflicting or malformed
    ``X-Astrolift-Organization`` header must 403 before this view starts
    any dev-environment work (#1791)."""
    client = Client()
    selected = str(other_org.guid) if header == "other-org" else "not-a-guid"
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {
            "runtime": "python",
            "runtime_version": "3.12",
            "start_command": "python main.py",
            "port": 8000,
            "resource_profile": "small",
        },
        {**auth_headers, "HTTP_X_ASTROLIFT_ORGANIZATION": selected},
    )
    assert r.status_code == 403, r.content
    assert "organization" in r.json()["detail"]
    assert workflow_starts == []
    assert not DevEnvironment.objects.exists()


def test_create_with_bad_runtime_returns_400(cluster, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {"runtime": "rust"},
        auth_headers,
    )
    assert r.status_code == 400
    assert "runtime" in r.json()["detail"]
    assert workflow_starts == []


def test_create_with_invalid_port_returns_400(cluster, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {"runtime": "python", "port": 999999},
        auth_headers,
    )
    assert r.status_code == 400
    assert "port" in r.json()["detail"]
    assert workflow_starts == []


def test_create_with_bad_resource_profile_returns_400(cluster, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {"runtime": "python", "resource_profile": "xxlarge"},
        auth_headers,
    )
    assert r.status_code == 400
    assert "resource_profile" in r.json()["detail"]
    assert workflow_starts == []


def test_create_without_cluster_available_returns_422(org, auth_headers, workflow_starts):
    # No cluster fixture → no managed cluster on the org.
    client = Client()
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {"runtime": "python"},
        auth_headers,
    )
    assert r.status_code == 422
    assert workflow_starts == []


def test_create_with_explicit_cluster_guid_returns_404_when_missing(cluster, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {"runtime": "python", "cluster_guid": "00000000-0000-0000-0000-000000000000"},
        auth_headers,
    )
    assert r.status_code == 404
    assert workflow_starts == []


def test_create_with_explicit_cluster_guid_binds(cluster, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        "/api/builder/v1/dev-environments/",
        {"runtime": "node", "cluster_guid": str(cluster.guid)},
        auth_headers,
    )
    assert r.status_code == 201
    dev = DevEnvironment.objects.get(guid=r.json()["id"])
    assert dev.tenant_cluster_id == cluster.id


def test_create_with_invalid_json_returns_400(cluster, auth_headers, workflow_starts):
    client = Client()
    r = client.post(
        "/api/builder/v1/dev-environments/",
        data=b"not json",
        content_type="application/json",
        **auth_headers,
    )
    assert r.status_code == 400


# ---- sync_dev_environment_files -------------------------------------


def _running_dev_env(org, cluster, user):
    dev = DevEnvironment.objects.create(
        organization=org,
        creator=user,
        tenant_cluster=cluster,
        runtime=DevEnvironment.Runtime.PYTHON,
        port=8080,
        status=DevEnvironment.Status.RUNNING,
        namespace="builder-dev-test",
        preview_url="https://example.invalid",
    )
    return dev


def test_sync_happy_path_updates_files_and_enqueues_workflow(
    org, cluster, user, auth_headers, workflow_starts
):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _put_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/files/",
        {"files": {"main.py": "print('hi')", "requirements.txt": "flask"}},
        auth_headers,
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "syncing"
    assert body["file_count"] == 2

    dev.refresh_from_db()
    assert dev.status == DevEnvironment.Status.SYNCING
    assert dev.files == {"main.py": "print('hi')", "requirements.txt": "flask"}

    assert len(workflow_starts) == 1
    start = workflow_starts[0]
    assert start["name"] == "SyncDevEnvironmentFilesWorkflow"
    assert start["workflow_id"].startswith(f"SyncDevEnvironmentFilesWorkflow-{dev.guid}-")


def test_sync_when_not_running_returns_409(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    dev.status = DevEnvironment.Status.CREATING
    dev.save(update_fields=["status", "updated_at", "version"])

    client = Client()
    r = _put_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/files/",
        {"files": {"main.py": "x"}},
        auth_headers,
    )
    assert r.status_code == 409
    assert workflow_starts == []


def test_sync_path_traversal_rejected(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _put_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/files/",
        {"files": {"../etc/passwd": "x"}},
        auth_headers,
    )
    assert r.status_code == 400
    assert ".." in r.json()["detail"]
    assert workflow_starts == []


def test_sync_absolute_path_rejected(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _put_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/files/",
        {"files": {"/etc/hosts": "x"}},
        auth_headers,
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    ("files", "needle"),
    [
        ({"static//logo.png": "x"}, "segments"),
        ({"./main.py": "x"}, "segments"),
        ({"static/": "x"}, "segments"),
        ({"static": "x", "static/logo.png": "y"}, "also a directory"),
    ],
)
def test_sync_rejects_a_tree_that_cannot_be_laid_out(
    org, cluster, user, auth_headers, workflow_starts, files, needle
):
    dev = _running_dev_env(org, cluster, user)
    r = _put_json(
        Client(), f"/api/builder/v1/dev-environments/{dev.guid}/files/", {"files": files}, auth_headers
    )
    assert r.status_code == 400
    assert needle in r.json()["detail"]
    assert workflow_starts == []


def test_sync_accepts_a_tree_with_directories(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    files = {"main.py": "x", "static/css/site.css": "body{}"}
    r = _put_json(
        Client(), f"/api/builder/v1/dev-environments/{dev.guid}/files/", {"files": files}, auth_headers
    )
    assert r.status_code == 200, r.content
    dev.refresh_from_db()
    assert dev.files == files


def test_sync_too_large_rejected(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    # 600 KiB single file > 512 KiB cap
    big = "x" * (600 * 1024)
    client = Client()
    r = _put_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/files/",
        {"files": {"big.bin": big}},
        auth_headers,
    )
    assert r.status_code == 400
    assert "exceeds" in r.json()["detail"]
    assert workflow_starts == []


def test_sync_too_many_files_rejected(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    files = {f"f{i}.py": "x" for i in range(101)}
    client = Client()
    r = _put_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/files/",
        {"files": files},
        auth_headers,
    )
    assert r.status_code == 400
    assert "too many" in r.json()["detail"]
    assert workflow_starts == []


def test_sync_missing_env_returns_404(org, cluster, user, auth_headers, workflow_starts):
    client = Client()
    r = _put_json(
        client,
        "/api/builder/v1/dev-environments/00000000-0000-0000-0000-000000000000/files/",
        {"files": {"a.py": "x"}},
        auth_headers,
    )
    assert r.status_code == 404


def test_sync_other_org_env_returns_404(cluster, user, other_org, auth_headers, workflow_starts):
    """A dev env in another org must look like 404 (not 403) — don't
    leak existence across orgs."""
    other_user = User.objects.create_user(username="other", email="other@astrolift.dev", password="pw")
    dev = DevEnvironment.objects.create(
        organization=other_org,
        creator=other_user,
        tenant_cluster=cluster,
        runtime=DevEnvironment.Runtime.PYTHON,
        status=DevEnvironment.Status.RUNNING,
        namespace="builder-other",
    )
    client = Client()
    r = _put_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/files/",
        {"files": {"a.py": "x"}},
        auth_headers,
    )
    assert r.status_code == 404


# ---- promote_dev_environment ----------------------------------------


def test_promote_happy_path_creates_app_and_env_and_enqueues_onboard(
    org, cluster, user, team, auth_headers, workflow_starts
):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "My Promoted App"},
        auth_headers,
    )
    assert r.status_code == 202, r.content
    body = r.json()
    assert body["status"] == "promoting"
    assert body["app_slug"] == "my-promoted-app"

    app = RegisteredApp.objects.get(guid=body["app_guid"])
    assert app.organization_id == org.id
    assert app.team_id == team.id
    assert app.source_kind == RegisteredApp.SourceKind.DIRECT_UPLOAD
    assert app.default_tenant_cluster_id == cluster.id

    env = AppEnvironment.objects.get(registered_app=app)
    assert env.name == "production"
    assert env.tenant_cluster_id == cluster.id

    dev.refresh_from_db()
    assert dev.status == DevEnvironment.Status.PROMOTING
    assert dev.promoted_app_id == app.id

    assert [s["name"] for s in workflow_starts] == ["OnboardAppWorkflow", "DeployPromotedAppWorkflow"]
    onboard, deploy = workflow_starts
    assert onboard["workflow_id"] == f"OnboardAppWorkflow-{app.guid}"
    assert deploy["workflow_id"] == f"DeployPromotedAppWorkflow-{app.guid}"
    assert deploy["args"][0].dev_environment_id == dev.id
    assert deploy["args"][0].storage_class == ""
    assert body["app_url"] == "https://builder-org-my-promoted-app.builder-org.dev.astrolift.io"
    # No data file declared, so there is nothing to persist or warn about.
    assert body["data_persistent"] is None


def test_promote_with_explicit_slug_and_environment(org, cluster, user, team, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {
            "app_name": "Pinned",
            "app_slug": "pinned-slug",
            "environment_name": "staging",
        },
        auth_headers,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["app_slug"] == "pinned-slug"
    env = AppEnvironment.objects.get(registered_app__slug="pinned-slug")
    assert env.name == "staging"


def test_promote_same_slug_again_updates_the_app_in_place(
    org, cluster, user, team, auth_headers, workflow_starts
):
    """Chat Studio's re-ship: promote the same dev env into the app it
    already promoted to, matched by slug, updates it instead of 409ing (#1875)."""
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    first = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Chat Studio App"},
        auth_headers,
    )
    assert first.status_code == 202, first.content
    app_guid = first.json()["app_guid"]

    # The deploy that would flip this back to RUNNING never actually runs
    # in this test (Temporal is patched out) -- simulate it having
    # finished, which is the steady state a real re-ship lands in.
    dev.refresh_from_db()
    dev.status = DevEnvironment.Status.RUNNING
    dev.save(update_fields=["status", "updated_at", "version"])
    workflow_starts.clear()

    second = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Chat Studio App"},
        auth_headers,
    )

    assert second.status_code == 202, second.content
    assert second.json()["app_guid"] == app_guid
    assert RegisteredApp.objects.filter(organization=org, slug="chat-studio-app").count() == 1
    assert AppEnvironment.objects.filter(registered_app__guid=app_guid).count() == 1
    dev.refresh_from_db()
    assert dev.status == DevEnvironment.Status.PROMOTING
    # Onboarding is a one-time bootstrap; a re-promote only re-deploys.
    assert [s["name"] for s in workflow_starts] == ["DeployPromotedAppWorkflow"]


def test_promote_again_while_still_promoting_is_allowed_as_an_update(
    org, cluster, user, team, auth_headers, workflow_starts
):
    """The dev env's own status can still read PROMOTING (a row from before
    the #1875 fix, or a re-promote landing mid-flight) -- that must not
    permanently lock it out of ever being re-promoted."""
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    first = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Still Promoting"},
        auth_headers,
    )
    assert first.status_code == 202, first.content
    dev.refresh_from_db()
    assert dev.status == DevEnvironment.Status.PROMOTING

    second = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Still Promoting"},
        auth_headers,
    )
    assert second.status_code == 202, second.content


def test_promote_update_refuses_to_move_the_app_to_a_different_team(
    org, cluster, user, team, auth_headers, workflow_starts
):
    other_team = Team.objects.create(organization=org, name="Other", slug="other-team")
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    first = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Team Locked", "team_slug": team.slug},
        auth_headers,
    )
    assert first.status_code == 202, first.content
    dev.refresh_from_db()
    dev.status = DevEnvironment.Status.RUNNING
    dev.save(update_fields=["status", "updated_at", "version"])

    second = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Team Locked", "team_slug": other_team.slug},
        auth_headers,
    )
    assert second.status_code == 409
    assert "different team" in second.json()["detail"]


def test_promote_torn_down_returns_409(org, cluster, user, team, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    dev.status = DevEnvironment.Status.TORN_DOWN
    dev.save(update_fields=["status", "updated_at", "version"])

    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Whatever"},
        auth_headers,
    )
    assert r.status_code == 409


def test_promote_failed_env_is_promotable(org, cluster, user, team, auth_headers, workflow_starts):
    """FAILED is in the promote-allowed set so an operator can salvage
    a broken dev env without first re-provisioning it."""
    dev = _running_dev_env(org, cluster, user)
    dev.status = DevEnvironment.Status.FAILED
    dev.save(update_fields=["status", "updated_at", "version"])

    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Salvaged"},
        auth_headers,
    )
    assert r.status_code == 202


def test_promote_missing_app_name_returns_400(org, cluster, user, team, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {},
        auth_headers,
    )
    assert r.status_code == 400
    assert "app_name" in r.json()["detail"]


def test_promote_slug_conflict_returns_409(org, cluster, user, team, auth_headers, workflow_starts):
    # Existing app with same slug.
    RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Conflict",
        slug="conflict-slug",
        provisioning_status="ready",
    )
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Conflict", "app_slug": "conflict-slug"},
        auth_headers,
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_promote_invalid_slug_returns_400(org, cluster, user, team, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Bad", "app_slug": "Bad_Slug!"},
        auth_headers,
    )
    assert r.status_code == 400


def test_promote_unknown_team_slug_returns_404(org, cluster, user, team, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Targeted", "team_slug": "nope-team"},
        auth_headers,
    )
    assert r.status_code == 404


def test_promote_without_cluster_returns_409(org, user, team, auth_headers, workflow_starts):
    """A dev env that lost its cluster (decommission) can't be promoted."""
    dev = DevEnvironment.objects.create(
        organization=org,
        creator=user,
        tenant_cluster=None,
        runtime=DevEnvironment.Runtime.PYTHON,
        status=DevEnvironment.Status.RUNNING,
        namespace="abandoned",
    )
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Stranded"},
        auth_headers,
    )
    assert r.status_code == 409


def test_promote_other_org_env_returns_404(cluster, user, other_org, auth_headers, workflow_starts):
    other_user = User.objects.create_user(username="other2", email="other2@astrolift.dev", password="pw")
    dev = DevEnvironment.objects.create(
        organization=other_org,
        creator=other_user,
        tenant_cluster=cluster,
        runtime=DevEnvironment.Runtime.PYTHON,
        status=DevEnvironment.Status.RUNNING,
        namespace="other-dev",
    )
    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Cross Org"},
        auth_headers,
    )
    assert r.status_code == 404


# ---- binary files + the data file (#1858) ----------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _sync(client, dev, payload, headers):
    return _put_json(client, f"/api/builder/v1/dev-environments/{dev.guid}/files/", payload, headers)


def test_sync_accepts_binary_files_with_a_declared_encoding(
    org, cluster, user, auth_headers, workflow_starts
):
    dev = _running_dev_env(org, cluster, user)
    png = _b64(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")

    r = _sync(
        Client(),
        dev,
        {"files": {"server.py": "print('hi')", "logo.png": {"content": png, "encoding": "base64"}}},
        auth_headers,
    )

    assert r.status_code == 200, r.content
    dev.refresh_from_db()
    assert dev.files == {"server.py": "print('hi')", "logo.png": {"content": png, "encoding": "base64"}}
    assert dev.data_file_path == ""


@pytest.mark.parametrize(
    "entry",
    [
        {"content": "not base64!", "encoding": "base64"},
        {"content": "aGk=\n", "encoding": "base64"},
        {"content": "aGk=", "encoding": "hex"},
        {"content": "aGk="},
        {"content": 7, "encoding": "base64"},
    ],
)
def test_sync_rejects_a_binary_file_without_valid_base64(
    org, cluster, user, auth_headers, workflow_starts, entry
):
    dev = _running_dev_env(org, cluster, user)

    r = _sync(Client(), dev, {"files": {"logo.png": entry}}, auth_headers)

    assert r.status_code == 400
    assert "logo.png" in r.json()["detail"]
    assert workflow_starts == []


def test_sync_counts_binary_files_by_decoded_size(org, cluster, user, auth_headers, workflow_starts):
    """400 KiB of binary is 533 KiB of base64; the 512 KiB cap is on the
    decoded bytes, so it fits. 600 KiB decoded does not."""
    dev = _running_dev_env(org, cluster, user)
    client = Client()

    fits = _sync(
        client,
        dev,
        {"files": {"a.bin": {"content": _b64(b"\x00" * 400 * 1024), "encoding": "base64"}}},
        auth_headers,
    )
    assert fits.status_code == 200, fits.content

    dev.status = DevEnvironment.Status.RUNNING
    dev.save(update_fields=["status", "updated_at", "version"])
    too_big = _sync(
        client,
        dev,
        {"files": {"a.bin": {"content": _b64(b"\x00" * 600 * 1024), "encoding": "base64"}}},
        auth_headers,
    )
    assert too_big.status_code == 400
    assert "exceeds" in too_big.json()["detail"]


def test_sync_stores_a_10_mib_data_file(org, cluster, user, auth_headers, workflow_starts):
    """The body is ~14 MiB, well past Django's 2.5 MiB
    DATA_UPLOAD_MAX_MEMORY_SIZE that ``request.body`` enforces."""
    dev = _running_dev_env(org, cluster, user)
    data = bytes(range(256)) * (10 * 1024 * 4)

    r = _sync(
        Client(),
        dev,
        {
            "files": {"server.py": "print('hi')"},
            "data_file": {"path": "data.sqlite", "content": _b64(data), "encoding": "base64"},
        },
        auth_headers,
    )

    assert r.status_code == 200, r.content
    dev.refresh_from_db()
    assert dev.data_file_path == "data.sqlite"
    assert bytes(dev.data_file) == data
    assert dev.files == {"server.py": "print('hi')"}
    assert len(workflow_starts) == 1


def test_sync_keeps_the_data_file_when_omitted_and_removes_it_on_null(
    org, cluster, user, auth_headers, workflow_starts
):
    dev = _running_dev_env(org, cluster, user)
    client = Client()
    _sync(
        client,
        dev,
        {"files": {}, "data_file": {"path": "db.sqlite", "content": _b64(b"rows"), "encoding": "base64"}},
        auth_headers,
    )

    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    assert _sync(client, dev, {"files": {"a.py": "x"}}, auth_headers).status_code == 200
    dev.refresh_from_db()
    assert (dev.data_file_path, bytes(dev.data_file)) == ("db.sqlite", b"rows")

    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    assert _sync(client, dev, {"files": {"a.py": "x"}, "data_file": None}, auth_headers).status_code == 200
    dev.refresh_from_db()
    assert (dev.data_file_path, dev.data_file) == ("", None)


@pytest.mark.parametrize(
    ("data_file", "fragment"),
    [
        ("data.sqlite", "data_file must be an object"),
        ({"path": "../data.sqlite", "content": "aGk=", "encoding": "base64"}, "data_file: path"),
        ({"path": "/data.sqlite", "content": "aGk=", "encoding": "base64"}, "data_file: path"),
        ({"path": "x" * 256, "content": "aGk=", "encoding": "base64"}, "at most 255"),
        ({"path": "data.sqlite", "content": "aGk="}, "base64"),
        ({"path": "data.sqlite", "content": "@@@@", "encoding": "base64"}, "base64"),
    ],
)
def test_sync_rejects_a_malformed_data_file(
    org, cluster, user, auth_headers, workflow_starts, data_file, fragment
):
    dev = _running_dev_env(org, cluster, user)

    r = _sync(Client(), dev, {"files": {}, "data_file": data_file}, auth_headers)

    assert r.status_code == 400
    assert fragment in r.json()["detail"]
    dev.refresh_from_db()
    assert dev.status == DevEnvironment.Status.RUNNING
    assert workflow_starts == []


@override_config(BUILDER_DATA_FILE_MAX_BYTES=1024)
def test_sync_enforces_the_configurable_data_file_limit(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    client = Client()

    at_limit = _sync(
        client,
        dev,
        {"files": {}, "data_file": {"path": "d.db", "content": _b64(b"x" * 1024), "encoding": "base64"}},
        auth_headers,
    )
    assert at_limit.status_code == 200, at_limit.content

    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)
    over = _sync(
        client,
        dev,
        {"files": {}, "data_file": {"path": "d.db", "content": _b64(b"x" * 1025), "encoding": "base64"}},
        auth_headers,
    )
    assert over.status_code == 400
    assert "1024 byte limit" in over.json()["detail"]


@override_config(BUILDER_DATA_FILE_MAX_BYTES=1024)
def test_sync_refuses_a_body_larger_than_the_limits_allow_before_reading_it(
    org, cluster, user, auth_headers, workflow_starts
):
    dev = _running_dev_env(org, cluster, user)
    # 1024-byte data cap: 4/3 of it + 6x the 512 KiB files cap + 1 MiB.
    limit = 1368 + 6 * 512 * 1024 + 1024 * 1024

    r = _sync(Client(), dev, {"files": {"big.txt": "x" * (limit + 1)}}, auth_headers)

    assert r.status_code == 413
    assert str(limit) in r.json()["detail"]
    dev.refresh_from_db()
    assert dev.status == DevEnvironment.Status.RUNNING


class _StorageDriver:
    """Answers the promote-time storage probe the way a cluster would."""

    def __init__(self, storage_classes, csi_drivers=()):
        self.storage_classes = storage_classes
        self.csi_drivers = list(csi_drivers)
        self.calls = 0

    def list_storage_classes(self, cluster):
        self.calls += 1
        return self.storage_classes

    def list_csi_drivers(self, cluster):
        return self.csi_drivers


def _promote_with_data(org, cluster, user, auth_headers, monkeypatch, storage_driver):
    if storage_driver is not None:
        monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: storage_driver)
    dev = _running_dev_env(org, cluster, user)
    DevEnvironment.objects.filter(pk=dev.pk).update(data_file_path="data.sqlite", data_file=b"rows")
    return _post_json(
        Client(),
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Sales"},
        auth_headers,
    )


def test_promote_reports_persistent_data_when_the_cluster_can_provision_it(
    org, cluster, user, team, auth_headers, workflow_starts, monkeypatch
):
    driver = _StorageDriver(
        [
            StorageClassInfo(name="standard", is_default=False, provisioner="rancher.io/local-path"),
            StorageClassInfo(name="gp3", is_default=True, provisioner="ebs.csi.aws.com"),
        ],
        csi_drivers=["ebs.csi.aws.com"],
    )

    r = _promote_with_data(org, cluster, user, auth_headers, monkeypatch, driver)

    assert r.status_code == 202, r.content
    assert r.json()["data_persistent"] is True
    [deploy] = [s for s in workflow_starts if s["name"] == "DeployPromotedAppWorkflow"]
    assert deploy["args"][0].storage_class == "gp3"


@pytest.mark.parametrize(
    ("storage_classes", "csi_drivers"),
    [
        # The conflict install (installer#315): no StorageClass at all.
        ([], []),
        # EKS's in-tree gp2 without the EBS CSI driver: claims sit Pending.
        ([StorageClassInfo(name="gp2", is_default=True, provisioner="kubernetes.io/aws-ebs")], []),
        # A CSI class whose driver is not installed.
        ([StorageClassInfo(name="rbd", is_default=True, provisioner="rook-ceph.rbd.csi.ceph.com")], []),
        # Static local volumes only; nothing provisions a new claim.
        ([StorageClassInfo(name="local", is_default=True, provisioner="kubernetes.io/no-provisioner")], []),
        # Several classes and no default: no safe pick.
        (
            [
                StorageClassInfo(name="a", is_default=False, provisioner="rancher.io/local-path"),
                StorageClassInfo(name="b", is_default=False, provisioner="rancher.io/local-path"),
            ],
            [],
        ),
    ],
)
def test_promote_falls_back_to_an_empty_dir_without_persistent_volumes(
    org, cluster, user, team, auth_headers, workflow_starts, monkeypatch, storage_classes, csi_drivers
):
    r = _promote_with_data(
        org, cluster, user, auth_headers, monkeypatch, _StorageDriver(storage_classes, csi_drivers)
    )

    assert r.status_code == 202, r.content
    assert r.json()["data_persistent"] is False
    [deploy] = [s for s in workflow_starts if s["name"] == "DeployPromotedAppWorkflow"]
    assert deploy["args"][0].storage_class == ""


def test_promote_treats_an_unreachable_cluster_as_no_persistent_volumes(
    org, cluster, user, team, auth_headers, workflow_starts, monkeypatch
):
    # No driver patch: the fixture cluster's plugin cannot be built.
    r = _promote_with_data(org, cluster, user, auth_headers, monkeypatch, None)

    assert r.status_code == 202, r.content
    assert r.json()["data_persistent"] is False


def test_promote_without_a_data_file_does_not_probe_storage(
    org, cluster, user, team, auth_headers, workflow_starts, monkeypatch
):
    driver = _StorageDriver([StorageClassInfo(name="gp3", is_default=True, provisioner="ebs.csi.aws.com")])
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: driver)
    dev = _running_dev_env(org, cluster, user)

    r = _post_json(
        Client(),
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Static"},
        auth_headers,
    )

    assert r.status_code == 202, r.content
    assert r.json()["data_persistent"] is None
    assert driver.calls == 0


# ---- acceptance (#1858): upload a 10 MiB data.sqlite, the promoted app reads it --


def test_uploaded_sqlite_reaches_the_promoted_app_intact(
    org, cluster, user, team, auth_headers, workflow_starts, monkeypatch, tmp_path
):
    """End to end short of a live cluster: a 10 MiB SQLite file goes
    through the real files endpoint, promote finds a provisionable
    StorageClass, the promoted-app activity renders the runtime, and the
    seed init container's own shell rebuilds a file that SQLite opens with
    every row intact."""
    driver = _RecordingDriver(
        storage_classes=[StorageClassInfo(name="gp3", is_default=True, provisioner="ebs.csi.aws.com")],
        csi_drivers=["ebs.csi.aws.com"],
    )
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: driver)
    data = _sqlite_bytes(tmp_path, rows=160, row_bytes=64 * 1024)
    assert len(data) >= 10 * 1024 * 1024
    dev = _running_dev_env(org, cluster, user)
    client = Client()

    synced = _sync(
        client,
        dev,
        {
            "files": {"server.py": "print('serve')"},
            "data_file": {"path": "data.sqlite", "content": _b64(data), "encoding": "base64"},
        },
        auth_headers,
    )
    assert synced.status_code == 200, synced.content
    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)

    promoted = _post_json(
        client, f"/api/builder/v1/dev-environments/{dev.guid}/promote/", {"app_name": "Sales"}, auth_headers
    )
    assert promoted.status_code == 202, promoted.content
    assert promoted.json()["data_persistent"] is True
    [deploy] = [s for s in workflow_starts if s["name"] == "DeployPromotedAppWorkflow"]

    _deploy_promoted_app_sync(deploy["args"][0].dev_environment_id, deploy["args"][0].storage_class)

    _, resources = driver.applied[-1]
    assert _by_kind(resources, "PersistentVolumeClaim")[0]["spec"]["storageClassName"] == "gp3"
    pod = _by_kind(resources, "Deployment")[0]["spec"]["template"]["spec"]
    assert {e["name"]: e["value"] for e in pod["containers"][0]["env"]}[
        "ASTROLIFT_DATA_FILE"
    ] == "/data/data.sqlite"
    target = tmp_path / "data" / "data.sqlite"
    _seed(resources, str(target), tmp_path)
    assert target.read_bytes() == data
    conn = sqlite3.connect(target)
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert conn.execute("SELECT count(*), sum(length(payload)) FROM sales").fetchone() == (
        160,
        160 * 64 * 1024,
    )
    conn.close()
