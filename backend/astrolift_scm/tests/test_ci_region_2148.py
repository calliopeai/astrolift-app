"""Actual app/cluster coordinates govern the workflow that is previewed and pushed."""

import pytest
import yaml
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import build_ci_workflow_sync_status
from astrolift_scm.services import workflow_sync

pytestmark = pytest.mark.django_db


@pytest.fixture
def app():
    org = Organization.objects.create(name="Region owner", slug="region-owner")
    team = Team.objects.create(organization=org, name="Region team", slug="region-team")
    plugin, _ = ProviderPlugin.objects.get_or_create(slug="aws", defaults={"name": "AWS"})
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        slug="region-cluster",
        name="Region cluster",
        region="eu-west-1",
    )
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Region app",
        slug="region-app",
        default_tenant_cluster=cluster,
        source_kind="github",
        source_repo="owner/region-app",
        deploy_branch="release/eu",
        registry_repo_uri="123456789012.dkr.ecr.eu-west-1.amazonaws.com/owner/region-app",
        push_role_ref="arn:aws:iam::123456789012:role/region-push",
    )


def _workflow(app):
    return yaml.safe_load(workflow_sync.render_astrolift_ci_workflow(app))


def _steps(body):
    return body["jobs"]["build-and-deploy"]["steps"]


@pytest.mark.parametrize("region", ["eu-west-1", "ap-southeast-2"])
def test_actual_registry_region_controls_auth_and_read_preview(app, region):
    app.registry_repo_uri = f"123456789012.dkr.ecr.{region}.amazonaws.com/owner/region-app"
    app.save()
    app.refresh_from_db()
    body = workflow_sync.render_astrolift_ci_workflow(app)
    assert build_ci_workflow_sync_status(app).rendered_text == body
    parsed = yaml.safe_load(body)
    auth = next(step for step in _steps(parsed) if step.get("uses", "").startswith("aws-actions/configure"))
    assert auth["with"]["aws-region"] == region
    build = next(step for step in _steps(parsed) if step.get("name") == "Build and push image")
    assert build["env"]["IMAGE"] == f"{app.registry_repo_uri}:${{{{ github.sha }}}}"
    assert parsed[True]["push"]["branches"] == [app.deploy_branch]


def test_deploy_only_uses_cluster_region_when_configured(app):
    app.registry_repo_uri = ""
    auth = next(step for step in _steps(_workflow(app)) if step.get("with"))
    assert auth["with"]["aws-region"] == "eu-west-1"
    assert not any(step.get("name") == "Build and push image" for step in _steps(_workflow(app)))


@pytest.mark.parametrize("unconfigured", ["no_cluster", "missing_region"])
def test_unconfigured_deploy_only_needs_no_aws_or_invented_region(app, unconfigured):
    app.registry_repo_uri = ""
    if unconfigured == "no_cluster":
        app.default_tenant_cluster = None
    else:
        app.default_tenant_cluster.region = ""
        app.default_tenant_cluster.save()
    body = workflow_sync.render_astrolift_ci_workflow(app)
    assert "aws-actions/" not in body
    assert "aws-region" not in body
    assert "us-west-2" not in body
    assert any(step.get("name") == "Notify Astrolift" for step in _steps(yaml.safe_load(body)))


def test_private_ecr_region_is_authoritative_without_default_cluster(app):
    app.default_tenant_cluster = None
    auth = next(step for step in _steps(_workflow(app)) if step.get("with"))
    assert auth["with"]["aws-region"] == "eu-west-1"


@pytest.mark.parametrize("invalid", ["foreign", "deleted", "inactive", "non_aws"])
def test_incoherent_cluster_refuses_preview_and_sync_before_driver_or_repo_calls(app, invalid, monkeypatch):
    cluster = app.default_tenant_cluster
    if invalid == "foreign":
        cluster.organization = Organization.objects.create(name="Sibling", slug="region-sibling")
    elif invalid == "deleted":
        cluster.deleted_at = timezone.now()
    elif invalid == "inactive":
        cluster.is_active = False
    else:
        cluster.provider_plugin, _ = ProviderPlugin.objects.get_or_create(
            slug="gcp", defaults={"name": "GCP"}
        )
    cluster.save()
    app.refresh_from_db()
    calls = []
    monkeypatch.setattr(workflow_sync, "ensure_ci_push_role", lambda *a: calls.append("driver"))
    monkeypatch.setattr(workflow_sync, "_sync_github", lambda *a, **k: calls.append("repo"))
    assert build_ci_workflow_sync_status(app).rendered_text == ""
    with pytest.raises(workflow_sync.WorkflowSyncError) as exc:
        workflow_sync.sync_workflow_file_to_repo(app)
    assert exc.value.code == "CI_CONFIGURATION_INVALID"
    assert calls == []


def test_shared_live_cluster_remains_supported(app):
    app.default_tenant_cluster.organization = None
    app.default_tenant_cluster.save()
    assert "eu-west-1" in workflow_sync.render_astrolift_ci_workflow(app)


@pytest.mark.parametrize(
    "uri", ["registry.example/app", "123456789012.dkr.ecr..amazonaws.com/app", "public.ecr.aws/owner/app"]
)
def test_non_private_ecr_coordinate_refuses_instead_of_defaulting(app, uri):
    app.registry_repo_uri = uri
    with pytest.raises(ValueError, match="private ECR"):
        workflow_sync.render_astrolift_ci_workflow(app)
    assert build_ci_workflow_sync_status(app).rendered_text == ""


def test_yaml_scalars_keep_quotes_newlines_and_branch_delimiters_as_data(app, settings):
    app.deploy_branch = 'release/"eu", other]\nname: injected'
    app.push_role_ref = 'arn:role/"quoted"\n# data'
    settings.PLATFORM_API_URL = 'https://platform.example/path?name="quoted"'
    parsed = _workflow(app)
    assert parsed[True]["push"]["branches"] == [app.deploy_branch]
    auth = next(step for step in _steps(parsed) if step.get("with"))
    assert auth["with"]["role-to-assume"] == app.push_role_ref
    notify = next(step for step in _steps(parsed) if step.get("name") == "Notify Astrolift")
    assert notify["env"]["API_URL"] == settings.PLATFORM_API_URL
    assert parsed["name"] == "astrolift deploy"


def test_github_expression_in_config_is_refused(app):
    app.deploy_branch = "${{ secrets.DEPLOY_TOKEN }}"
    with pytest.raises(ValueError, match="expressions"):
        workflow_sync.render_astrolift_ci_workflow(app)
