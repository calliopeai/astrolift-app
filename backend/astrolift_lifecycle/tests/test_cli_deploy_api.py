"""Tests for the Astrolift CLI + CI deploy REST surface (#777).

Covers the two endpoints exposed by ``astrolift_lifecycle.cli_views``:

* ``POST /api/cli/v1/apps/<slug>/deploy/``        — ``cli_views.ci_deploy``
* ``GET  /api/cli/v1/deployments/<guid>/status/`` — ``cli_views.deployment_status``

Both endpoints use the deploy-token bearer scheme
(``Authorization: Bearer alft_dt_...``); we mint real DeployToken rows via
``deploy_tokens.issue_token`` and exercise the auth path end-to-end rather
than stubbing ``verify_token``. Temporal is patched at
``astrolift_workflows.client.start_workflow`` so the tests don't need a
running Temporal server.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.deploy_tokens import issue_token
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp

pytestmark = pytest.mark.django_db


# A manifest with a single declared workload ``web`` so the CI endpoint's
# image-tag validation has something to match against. Without a parseable
# manifest the validator relaxes and accepts any workload slug, which would
# hide bugs in the workload-mismatch path.
_MANIFEST_WITH_WEB = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""


# ---- fixtures --------------------------------------------------------


@pytest.fixture
def app_with_manifest(app):
    """The conftest ``app`` row with a parseable manifest stamped on it so
    the CI endpoint resolves ``web`` as a declared workload."""
    app.manifest_raw = _MANIFEST_WITH_WEB
    app.save(update_fields=["manifest_raw", "updated_at", "version"])
    return app


@pytest.fixture
def deploy_token(app_with_manifest):
    """Issue a real DeployToken on the conftest app; return ``(row, plaintext)``."""
    return issue_token(app=app_with_manifest, name="ci-test", scopes=["app.deploy"])


@pytest.fixture
def auth_headers(deploy_token):
    _, plaintext = deploy_token
    return {"HTTP_AUTHORIZATION": f"Bearer {plaintext}"}


@pytest.fixture
def workflow_starts(monkeypatch):
    """Capture every ``start_workflow`` call so assertions can inspect them.

    Patches the canonical module path so the lazy import inside
    ``cli_views.ci_deploy`` picks up the stub.
    """
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


def _valid_body(**overrides):
    base = {
        "image_tags": {"web": "abc1234567890"},
        "commit_sha": "a" * 40,
        "branch": "main",
        "environment": "prod",
        "trigger_kind": "ci",
    }
    base.update(overrides)
    return base


# ---- smoke import ----------------------------------------------------


def test_cli_views_module_imports():
    """The view module is part of the URL conf; a syntax error or
    import-time crash would mask every other failure with a generic
    500 from the URL resolver. Pin importability directly."""
    import astrolift_lifecycle.cli_views as cli_views  # noqa: F401

    assert hasattr(cli_views, "ci_deploy")
    assert hasattr(cli_views, "deployment_status")


# ---- ci_deploy: happy path -------------------------------------------


def test_ci_deploy_happy_path_returns_201_and_starts_workflow(
    app_with_manifest, env, deploy_token, auth_headers, workflow_starts
):
    token_row, _ = deploy_token
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(),
        auth_headers,
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == Deployment.Status.PENDING.value
    assert body["deployment_id"]
    assert body["polling_url"] == f"/api/cli/v1/deployments/{body['deployment_id']}/status/"
    assert body["workflow_run_id"].startswith("run-")

    # Deployment row persisted with CI provenance + token attribution.
    deployment = Deployment.objects.get(guid=body["deployment_id"])
    assert deployment.registered_app_id == app_with_manifest.id
    assert deployment.app_environment_id == env.id
    assert deployment.status == Deployment.Status.PENDING.value
    assert deployment.commit_sha == "a" * 40
    assert deployment.branch == "main"
    assert deployment.trigger_kind == "ci"
    assert deployment.ci_actor_kind == "deploy_token"
    assert deployment.triggered_by_token_kind == "deploy_token"
    assert deployment.triggered_by_token_id == token_row.pk
    assert deployment.image_tag == "abc1234567890"

    # Exactly one workflow started, with a stable canonical ID.
    assert len(workflow_starts) == 1
    start = workflow_starts[0]
    assert start["name"] == "DeployAppWorkflow"
    assert start["workflow_id"] == (f"DeployAppWorkflow-{app_with_manifest.guid}-{env.guid}")


# ---- ci_deploy: auth failure modes -----------------------------------


def test_ci_deploy_missing_auth_returns_401(app_with_manifest, env, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(),
        {},
    )
    assert r.status_code == 401
    assert "deploy token" in r.json()["detail"].lower()
    assert workflow_starts == []
    assert Deployment.objects.count() == 0


def test_ci_deploy_bad_bearer_returns_401(app_with_manifest, env, workflow_starts):
    """A correctly-prefixed but unknown token must 401; without this guard
    the resolver would fall through to ``token.registered_app.slug`` on
    None and 500."""
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(),
        {"HTTP_AUTHORIZATION": "Bearer alft_dt_nope_this_is_not_a_real_token"},
    )
    assert r.status_code == 401
    assert "invalid" in r.json()["detail"].lower()
    assert workflow_starts == []


def test_ci_deploy_non_deploy_prefix_returns_401(app_with_manifest, env, workflow_starts):
    """A non-deploy-token bearer (e.g. an API token ``alft_at_...``) must
    be rejected; the CI endpoint is deploy-token-only."""
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(),
        {"HTTP_AUTHORIZATION": "Bearer alft_at_some_other_kind"},
    )
    assert r.status_code == 401
    assert workflow_starts == []


def test_ci_deploy_cross_app_token_returns_403(
    org, team, project, cluster, app_with_manifest, env, auth_headers, workflow_starts
):
    """Token issued for ``hello-app`` must not be usable against another
    app — even one in the same org. This is the multi-tenant blast-radius
    invariant for app-scoped credentials."""
    other_app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Other App",
        slug="other-app",
        provisioning_status="ready",
        manifest_raw=_MANIFEST_WITH_WEB,
    )
    AppEnvironment.objects.create(
        registered_app=other_app,
        tenant_cluster=cluster,
        name="prod",
        url="https://other.example.com",
        required_approvals=0,
    )

    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{other_app.slug}/deploy/",
        _valid_body(),
        auth_headers,
    )
    assert r.status_code == 403
    assert "not authorized" in r.json()["detail"].lower()
    assert workflow_starts == []
    assert Deployment.objects.count() == 0


def test_ci_deploy_token_missing_scope_returns_403(app_with_manifest, env, workflow_starts):
    """A token whose ``scopes`` array doesn't include ``app.deploy`` is
    refused even when it's bound to the right app."""
    _row, plaintext = issue_token(
        app=app_with_manifest,
        name="read-only",
        scopes=["app.read"],
    )
    headers = {"HTTP_AUTHORIZATION": f"Bearer {plaintext}"}

    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(),
        headers,
    )
    assert r.status_code == 403
    assert "scope" in r.json()["detail"].lower()
    assert workflow_starts == []


# ---- ci_deploy: validation -------------------------------------------


def test_ci_deploy_missing_commit_sha_returns_400(app_with_manifest, env, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(commit_sha=""),
        auth_headers,
    )
    assert r.status_code == 400
    assert "commit_sha" in r.json()["detail"]
    assert workflow_starts == []
    assert Deployment.objects.count() == 0


def test_ci_deploy_short_commit_sha_returns_400(app_with_manifest, env, auth_headers, workflow_starts):
    """Short SHAs collide across the repo over time; the validator pins
    a 40-char minimum so CI must pass the full hash."""
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(commit_sha="abc1234"),
        auth_headers,
    )
    assert r.status_code == 400
    assert "too short" in r.json()["detail"]
    assert workflow_starts == []


def test_ci_deploy_missing_image_tags_returns_400(app_with_manifest, env, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(image_tags={}),
        auth_headers,
    )
    assert r.status_code == 400
    assert "image_tags" in r.json()["detail"]
    assert workflow_starts == []


def test_ci_deploy_invalid_branch_returns_400(app_with_manifest, env, auth_headers, workflow_starts):
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(branch="has spaces"),
        auth_headers,
    )
    assert r.status_code == 400
    assert workflow_starts == []


def test_ci_deploy_undeclared_workload_tag_returns_400(app_with_manifest, env, auth_headers, workflow_starts):
    """Tagging a workload not in the manifest is a typo defense — silent
    accept would mean the operator's intended workload didn't update."""
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(image_tags={"not-a-workload": "abc1234567890"}),
        auth_headers,
    )
    assert r.status_code == 400
    assert "not declared" in r.json()["detail"]
    assert workflow_starts == []


def test_ci_deploy_invalid_json_returns_400(app_with_manifest, env, auth_headers, workflow_starts):
    client = Client()
    r = client.post(
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        data=b"not json at all",
        content_type="application/json",
        **auth_headers,
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.json()["detail"]
    assert workflow_starts == []


def test_ci_deploy_unknown_environment_returns_400(app_with_manifest, env, auth_headers, workflow_starts):
    """``preod`` would silently deploy nothing if not validated; the
    validator refuses anything not in the registered-envs set."""
    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(environment="preod"),
        auth_headers,
    )
    assert r.status_code == 400
    assert "preod" in r.json()["detail"]
    assert workflow_starts == []


def test_ci_deploy_unknown_app_slug_returns_403(deploy_token, workflow_starts):
    """No app row for the slug — but the token is bound to ``hello-app``
    while the URL says ``ghost-app``, so the token/app scope guard fires
    first and returns 403 (the app-existence check is unreachable). Pins
    that the auth path doesn't accidentally leak existence by 404'ing
    before the scope check."""
    _, plaintext = deploy_token
    client = Client()
    r = _post_json(
        client,
        "/api/cli/v1/apps/ghost-app/deploy/",
        _valid_body(),
        {"HTTP_AUTHORIZATION": f"Bearer {plaintext}"},
    )
    assert r.status_code == 403
    assert workflow_starts == []


# ---- ci_deploy: pause gates ------------------------------------------


def test_ci_deploy_paused_environment_returns_409(app_with_manifest, env, auth_headers, workflow_starts):
    env.deploys_paused = True
    env.save(update_fields=["deploys_paused", "updated_at", "version"])

    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(),
        auth_headers,
    )
    assert r.status_code == 409
    assert "paused" in r.json()["detail"]
    assert workflow_starts == []
    assert Deployment.objects.count() == 0


def test_ci_deploy_webhook_paused_app_returns_409_for_ci_trigger(
    app_with_manifest, env, auth_headers, workflow_starts
):
    """The app-global webhook pause is keyed on ``trigger_kind``; a ``ci``
    delivery is webhook-shaped and must 409 when the flag is on, even
    though the env itself isn't paused."""
    app_with_manifest.webhook_deploys_paused = True
    app_with_manifest.webhook_deploys_pause_reason = "rolling out infra change"
    app_with_manifest.save(
        update_fields=[
            "webhook_deploys_paused",
            "webhook_deploys_pause_reason",
            "updated_at",
            "version",
        ]
    )

    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(trigger_kind="ci"),
        auth_headers,
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "paused" in detail
    assert "rolling out infra change" in detail
    assert workflow_starts == []


def test_ci_deploy_webhook_paused_app_allows_manual_cli(
    app_with_manifest, env, auth_headers, workflow_starts
):
    """The webhook-pause flag is an explicit on-call escape valve: it
    blocks webhook-shaped triggers but lets ``manual_cli`` through so the
    operator who set the flag can still ship a fix."""
    app_with_manifest.webhook_deploys_paused = True
    app_with_manifest.save(update_fields=["webhook_deploys_paused", "updated_at", "version"])

    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(trigger_kind="manual_cli"),
        auth_headers,
    )
    assert r.status_code == 201, r.content
    assert len(workflow_starts) == 1


# ---- deployment_status -----------------------------------------------


def _make_deployment(app, env, **overrides) -> Deployment:
    fields = {
        "registered_app": app,
        "app_environment": env,
        "trigger_kind": "ci",
        "status": Deployment.Status.PENDING.value,
        "image_tag": "abc1234567890",
        "commit_sha": "a" * 40,
        "branch": "main",
        "ci_actor_kind": "deploy_token",
    }
    fields.update(overrides)
    return Deployment.objects.create(**fields)


def test_deployment_status_returns_current_state(app_with_manifest, env, deploy_token):
    _, plaintext = deploy_token
    deployment = _make_deployment(app_with_manifest, env)

    client = Client()
    r = client.get(
        f"/api/cli/v1/deployments/{deployment.guid}/status/",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["deployment_id"] == str(deployment.guid)
    assert body["status"] == Deployment.Status.PENDING.value
    assert body["image_tag"] == "abc1234567890"
    assert body["commit_sha"] == "a" * 40
    assert body["branch"] == "main"
    assert body["environment"] == "prod"
    assert body["created_at"] is not None
    assert body["updated_at"] is not None
    # ``message`` is empty since Deployment has no status_message column.
    assert body["message"] == ""


def test_deployment_status_reflects_transitions(app_with_manifest, env, deploy_token):
    """After ``transition_to(DEPLOYING)`` the status endpoint must show
    the new state — a polling client uses this to know when to stop."""
    _, plaintext = deploy_token
    deployment = _make_deployment(app_with_manifest, env)
    deployment.transition_to(Deployment.Status.DEPLOYING)

    client = Client()
    r = client.get(
        f"/api/cli/v1/deployments/{deployment.guid}/status/",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
    )
    assert r.status_code == 200
    assert r.json()["status"] == Deployment.Status.DEPLOYING.value


def test_deployment_status_missing_auth_returns_401(app_with_manifest, env):
    deployment = _make_deployment(app_with_manifest, env)

    client = Client()
    r = client.get(f"/api/cli/v1/deployments/{deployment.guid}/status/")
    assert r.status_code == 401


def test_deployment_status_bad_token_returns_401(app_with_manifest, env):
    deployment = _make_deployment(app_with_manifest, env)

    client = Client()
    r = client.get(
        f"/api/cli/v1/deployments/{deployment.guid}/status/",
        HTTP_AUTHORIZATION="Bearer alft_dt_unknown_token_value",
    )
    assert r.status_code == 401


def test_deployment_status_unknown_guid_returns_404(deploy_token):
    _, plaintext = deploy_token

    client = Client()
    r = client.get(
        "/api/cli/v1/deployments/00000000-0000-0000-0000-000000000000/status/",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
    )
    assert r.status_code == 404


def test_deployment_status_cross_app_token_returns_404(
    org, team, project, cluster, app_with_manifest, env, deploy_token
):
    """A token scoped to ``hello-app`` querying a deployment that belongs
    to a different app must look like 404, not 403 — leaking existence
    across the app boundary would let an attacker enumerate deployment
    GUIDs across the tenant."""
    other_app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Other App 2",
        slug="other-app-2",
        provisioning_status="ready",
    )
    other_env = AppEnvironment.objects.create(
        registered_app=other_app,
        tenant_cluster=cluster,
        name="prod",
        url="https://other2.example.com",
        required_approvals=0,
    )
    other_deployment = _make_deployment(other_app, other_env)

    _, plaintext = deploy_token  # token belongs to ``hello-app``

    client = Client()
    r = client.get(
        f"/api/cli/v1/deployments/{other_deployment.guid}/status/",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
    )
    assert r.status_code == 404


def test_deployment_status_soft_deleted_returns_404(app_with_manifest, env, deploy_token):
    """Soft-deleted Deployment rows must look like 404 — the polling URL
    is a public-ish surface and the soft-delete is the model's way of
    saying ``no longer addressable``."""
    from django.utils import timezone

    _, plaintext = deploy_token
    deployment = _make_deployment(app_with_manifest, env)
    deployment.deleted_at = timezone.now()
    deployment.save(update_fields=["deleted_at", "updated_at", "version"])

    client = Client()
    r = client.get(
        f"/api/cli/v1/deployments/{deployment.guid}/status/",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
    )
    assert r.status_code == 404


# ---- ci_deploy: regression around polling URL --------------------------


def test_ci_deploy_polling_url_resolves_to_status_endpoint(
    app_with_manifest, env, deploy_token, auth_headers, workflow_starts
):
    """End-to-end: the CLI creates a deploy, follows the returned
    ``polling_url`` with the same bearer, and gets back the live row.
    Pins the contract between the two endpoints."""
    _, plaintext = deploy_token
    client = Client()
    create = _post_json(
        client,
        f"/api/cli/v1/apps/{app_with_manifest.slug}/deploy/",
        _valid_body(),
        auth_headers,
    )
    assert create.status_code == 201, create.content
    polling_url = create.json()["polling_url"]

    status = client.get(
        polling_url,
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
    )
    assert status.status_code == 200
    assert status.json()["deployment_id"] == create.json()["deployment_id"]
    assert status.json()["status"] == Deployment.Status.PENDING.value


# ---- ci_deploy: cross-org isolation ----------------------------------


def test_ci_deploy_other_org_app_with_own_token_succeeds(provider_plugin, workflow_starts):
    """Sanity-check the app-scoping is by row identity, not by org —
    a separate org with its own app + token can deploy independently
    even when slug names collide with another org's app."""
    other_org = Organization.objects.create(name="Globex", slug="globex-test")
    other_team = Team.objects.create(organization=other_org, name="Eng", slug="eng-2")
    other_project = Project.objects.create(
        organization=other_org,
        team=other_team,
        name="Demo",
        slug="demo-2",
    )
    other_cluster = TenantCluster.objects.create(
        organization=other_org,
        name="globex-cluster",
        slug="globex-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://globex.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    other_app = RegisteredApp.objects.create(
        organization=other_org,
        project=other_project,
        team=other_team,
        name="Globex App",
        slug="globex-app",
        provisioning_status="ready",
        manifest_raw=_MANIFEST_WITH_WEB,
    )
    AppEnvironment.objects.create(
        registered_app=other_app,
        tenant_cluster=other_cluster,
        name="prod",
        url="https://globex.example.com",
        required_approvals=0,
    )
    _, plaintext = issue_token(app=other_app, name="globex-ci", scopes=["app.deploy"])

    client = Client()
    r = _post_json(
        client,
        f"/api/cli/v1/apps/{other_app.slug}/deploy/",
        _valid_body(),
        {"HTTP_AUTHORIZATION": f"Bearer {plaintext}"},
    )
    assert r.status_code == 201, r.content
    assert len(workflow_starts) == 1
