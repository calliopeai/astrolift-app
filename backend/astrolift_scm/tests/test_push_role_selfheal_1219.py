"""Push-role self-heal on every workflow-push path (#1219) + notify-route
existence (#1220).

#1219: ``sync_workflow_file_to_repo`` must provision a missing OIDC push
role BEFORE rendering — every caller (Settings "Sync workflow file",
autowire, drift repair) funnels through it, so an app whose provision-time
role creation silently failed heals on the next sync instead of re-pushing
a workflow with a blank ``role-to-assume`` forever. A provisioning FAILURE
must refuse the push (a workflow that can never authenticate should not
land looking healthy).

#1220: the rendered notify step must target a URL that actually resolves
to a Django view — the previous template POSTed a fictional
``/api/v1/deploys`` which no route served, so a fully green CI run never
registered a deploy.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from django.urls import Resolver404, resolve

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.services.workflow_sync import (
    WorkflowSyncError,
    WorkflowSyncResult,
    ensure_ci_push_role,
    render_astrolift_ci_workflow,
    sync_workflow_file_to_repo,
)

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
    return Organization.objects.create(name="Acme", slug="acme-pr")


@pytest.fixture
def cluster(org):
    # Seeded directly; the plugin row is scaffolding for this test.
    plugin = ProviderPlugin(
        name="Test Provider",
        slug="aws",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="aws")
    return TenantCluster.objects.create(
        organization=org,
        name="dev",
        slug="dev-pr",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def app(org, cluster):
    team = Team.objects.create(organization=org, name="Eng", slug="eng-pr")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-pr")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="app-pr",
        source_kind="github",
        source_repo="acme/api",
        default_tenant_cluster=cluster,
        registry_repo_uri="111111111111.dkr.ecr.us-west-2.amazonaws.com/acme-pr/app-pr",
    )


class _RoleDriver:
    """Registry-driver stub whose ``ensure_ci_push_role`` behaviour is scripted."""

    def __init__(self, *, role_ref="arn:aws:iam::111111111111:role/pr-push", raises=None):
        self.role_ref = role_ref
        self.raises = raises
        self.calls: list[dict] = []

    def ensure_ci_push_role(self, *, repo, scm_provider, scm_repo_full_name, scm_repo_numeric_ids=None):
        self.calls.append(
            {
                "repo": repo,
                "scm_provider": scm_provider,
                "scm_repo_full_name": scm_repo_full_name,
                "scm_repo_numeric_ids": scm_repo_numeric_ids,
            }
        )
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(role_ref=self.role_ref)


def _patch_driver(monkeypatch, driver):
    import core.app_deploy as app_deploy

    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda cluster, cap: driver)


# ---- ensure_ci_push_role unit behaviour --------------------------------


def test_ensure_provisions_and_persists_blank_ref(app, monkeypatch):
    driver = _RoleDriver()
    _patch_driver(monkeypatch, driver)

    assert ensure_ci_push_role(app) == ""

    app.refresh_from_db()
    assert app.push_role_ref == "arn:aws:iam::111111111111:role/pr-push"
    assert driver.calls == [
        {
            "repo": "acme-pr/app-pr",
            "scm_provider": "github",
            "scm_repo_full_name": "acme/api",
            # No SourceConnection in this fixture — the numeric-id lookup
            # degrades to None and the trust stays login-pattern-only (#1532).
            "scm_repo_numeric_ids": None,
        }
    ]


def test_ensure_reports_provisioning_failure(app, monkeypatch):
    _patch_driver(monkeypatch, _RoleDriver(raises=RuntimeError("CreateRole denied")))
    assert "CreateRole denied" in ensure_ci_push_role(app)
    app.refresh_from_db()
    assert app.push_role_ref == ""


def test_ensure_treats_blank_driver_ref_as_failure(app, monkeypatch):
    """A driver that supports push roles but returns nothing must NOT be a
    silent no-op — rendering would emit a blank role-to-assume (#1219)."""
    _patch_driver(monkeypatch, _RoleDriver(role_ref=""))
    assert ensure_ci_push_role(app) != ""


def test_ensure_noop_without_cluster(app, monkeypatch):
    app.default_tenant_cluster = None
    app.save(update_fields=["default_tenant_cluster", "updated_at", "version"])
    _patch_driver(monkeypatch, _RoleDriver(raises=AssertionError("must not be called")))
    assert ensure_ci_push_role(app) == ""


def test_ensure_noop_when_driver_lacks_push_roles(app, monkeypatch):
    _patch_driver(monkeypatch, SimpleNamespace())  # no ensure_ci_push_role attr
    assert ensure_ci_push_role(app) == ""


# ---- sync path integration --------------------------------------------


def test_sync_refuses_to_push_when_provisioning_fails(app, monkeypatch):
    _patch_driver(monkeypatch, _RoleDriver(raises=RuntimeError("CreateRole denied")))
    with pytest.raises(WorkflowSyncError) as exc:
        sync_workflow_file_to_repo(app)
    assert exc.value.code == "PUSH_ROLE_PROVISION_FAILED"
    assert "CreateRole denied" in exc.value.message


def test_sync_heals_blank_ref_before_rendering(app, monkeypatch):
    """The healed ARN must be in the body the sync pushes — the whole point
    is that the Settings sync button stops re-pushing a broken file."""
    driver = _RoleDriver()
    _patch_driver(monkeypatch, driver)

    pushed: dict = {}

    def _fake_sync_github(app_arg, *, force_pr=False):
        pushed["rendered"] = render_astrolift_ci_workflow(app_arg)
        from astrolift_scm.services.workflow_sync import WorkflowSyncResult

        return WorkflowSyncResult(status="updated", commit_sha="abc")

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._sync_github",
        _fake_sync_github,
    )

    result = sync_workflow_file_to_repo(app)

    assert result.status == "updated"
    assert driver.calls, "sync must attempt push-role provisioning"
    assert 'role-to-assume: "arn:aws:iam::111111111111:role/pr-push"' in pushed["rendered"]


def test_agent_sync_does_not_provision_an_app_image_push_role(app, monkeypatch):
    app.is_agent = True
    app.manifest_path = "agents/demo/astrolift.toml"

    def _must_not_provision(_app):
        raise AssertionError("agent package validation does not push an app image")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.ensure_ci_push_role", _must_not_provision)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._sync_github",
        lambda _app, *, force_pr=False: WorkflowSyncResult(status="fetch_failed", error="expected test stop"),
    )

    result = sync_workflow_file_to_repo(app)

    assert result.status == "fetch_failed"


def test_non_github_agent_sync_fails_without_writing_app_workflow(app):
    app.is_agent = True
    app.source_kind = "gitlab"

    with pytest.raises(WorkflowSyncError) as exc:
        sync_workflow_file_to_repo(app)

    assert exc.value.code == "AGENT_CI_UNSUPPORTED"
    assert "left unchanged" in exc.value.message


# ---- notify route existence (#1220) ------------------------------------


def test_rendered_notify_urls_resolve_to_real_routes(settings):
    """Every platform API path the rendered workflow curls must resolve to a
    Django view. This is the class-level guard: the template can never again
    reference an endpoint that doesn't exist."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = render_astrolift_ci_workflow(
        SimpleNamespace(
            slug="hello-app",
            deploy_branch="main",
            registry_repo_uri="123456789012.dkr.ecr.us-west-2.amazonaws.com/hello-app",
            push_role_ref="arn:aws:iam::123456789012:role/push",
            source_kind="github",
            source_repo="acme/hello-app",
        )
    )

    # Paths appear as ``$API_URL/api/...`` (env-var indirection) — extract the
    # path portion up to the closing quote.
    paths = re.findall(r"\$API_URL(/api/[^\s\"']+)", body)
    assert paths, "expected the notify step to reference a platform API path"
    for path in paths:
        # Env-var placeholders inside the path (e.g. $APP_SLUG) stand in for
        # URL kwargs — substitute a plausible literal before resolving.
        concrete = re.sub(r"\$[A-Z_]+", "hello-app", path)
        try:
            match = resolve(concrete)
        except Resolver404:
            pytest.fail(f"workflow notify path {concrete!r} does not resolve to any Django route")
        assert match.func is not None


def test_rendered_workflow_never_references_the_fictional_endpoint(settings):
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    for uri in ("123456789012.dkr.ecr.us-west-2.amazonaws.com/hello-app", ""):
        body = render_astrolift_ci_workflow(
            SimpleNamespace(
                slug="hello-app",
                deploy_branch="main",
                registry_repo_uri=uri,
                push_role_ref="arn:aws:iam::123456789012:role/push",
                source_kind="github",
                source_repo="acme/hello-app",
            )
        )
        assert "/api/v1/deploys" not in body
