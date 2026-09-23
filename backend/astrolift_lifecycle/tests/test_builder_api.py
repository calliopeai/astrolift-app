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

import hashlib
import json
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.api_tokens import PLAINTEXT_PREFIX
from astrolift_identity.models import ApiToken, Member, Organization, OrganizationModule, Team
from astrolift_lifecycle.models import AppEnvironment, DevEnvironment
from astrolift_registry.models import RegisteredApp

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
def user(org):
    u = User.objects.create_user(username="builder-user", email="builder@astrolift.dev", password="pw")
    Member.objects.create(
        user=u,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
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

    assert len(workflow_starts) == 1
    start = workflow_starts[0]
    assert start["name"] == "OnboardAppWorkflow"
    assert start["workflow_id"] == f"OnboardAppWorkflow-{app.guid}"


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


def test_promote_already_promoted_returns_409(org, cluster, user, team, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    dev.status = DevEnvironment.Status.PROMOTING
    dev.save(update_fields=["status", "updated_at", "version"])

    client = Client()
    r = _post_json(
        client,
        f"/api/builder/v1/dev-environments/{dev.guid}/promote/",
        {"app_name": "Whatever"},
        auth_headers,
    )
    assert r.status_code == 409
    assert workflow_starts == []


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
