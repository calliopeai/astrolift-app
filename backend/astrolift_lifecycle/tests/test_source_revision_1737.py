"""Deployment input, approval, manifest fetch and image build share one resolved SHA."""

import importlib
import io
import json

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import DeploymentByIdInput, LifecycleMutation
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import revisions
from config.schema import schema
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
SHA = "ab" * 20
MUTATION = """mutation($input: StartDeploymentInput!) {
  startDeployment(input: $input) {
    ok errors { code message field }
    data { id status imageTag commitSha branch }
  }
}"""
MANIFEST = """name = "hello"
[[workloads]]
name = "web"
kind = "deployment"
[[workloads.containers]]
name = "app"
is_primary = true
image_ref = "nginx:1.27.5"
"""


@pytest.fixture
def source(app, org, monkeypatch):
    app.source_repo = "acme/web"
    app.build_mode = "platform_build"
    app.build_strategy = "dockerfile"
    app.deploy_branch = "release/v2"
    app.save()
    secret = encrypt_at_rest(b"source-revision-test-token")
    connection = SourceConnection.objects.create(
        organization=org,
        display_name="Source",
        kind="github_pat",
        account_login="acme",
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
    )
    seen = []

    def request(req, *, timeout):
        seen.append(req.full_url)
        assert req.get_header("Authorization") == "token source-revision-test-token"
        return io.BytesIO(json.dumps({"sha": SHA}).encode())

    monkeypatch.setattr(revisions.urllib.request, "urlopen", request)
    return connection, seen


def _start(org, actor, fake_info, app, env, **fields):
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=actor.pk)):
        result = schema.execute_sync(
            MUTATION,
            variable_values={"input": {"appSlug": app.slug, "environmentName": env.name, **fields}},
            context_value=fake_info.context,
        )
    assert not result.errors, result.errors
    return result.data["startDeployment"]


@pytest.mark.parametrize(
    "fields,expected_ref",
    [
        ({}, "release%2Fv2"),
        ({"sourceRef": "v2.0.0"}, "v2.0.0"),
        ({"sourceRef": SHA}, SHA),
        ({"branch": "hotfix"}, "hotfix"),
    ],
)
def test_omitted_tag_resolves_and_pins_immediate_workflow(
    org, actor, fake_info, app, env, source, permission_resolver, temporal_recorder, fields, expected_ref
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    result = _start(org, actor, fake_info, app, env, **fields)
    assert result["ok"], result
    dep = Deployment.objects.get(guid=result["data"]["id"])
    assert (dep.image_tag, dep.commit_sha) == (SHA, SHA)
    assert source[1] == ["https://api.github.com/repos/acme/web/commits/" + expected_ref]
    inp = temporal_recorder.starts[0][1][0]
    assert (inp.commit_sha, inp.image_tags) == (SHA, {"app": SHA})


@pytest.mark.parametrize(
    "mode,fields,expected_tag,expected_sha",
    [
        ("ci_pushed", {"imageTag": "v1", "commitSha": "abc123", "branch": "main"}, "v1", "abc123"),
        ("platform_build", {"imageTag": "custom", "commitSha": SHA}, "custom", SHA),
        ("platform_build", {"imageTag": "custom", "sourceRef": "v2"}, "custom", SHA),
        ("none", {}, "", ""),
    ],
)
def test_existing_tags_and_manifest_images_keep_their_contract(
    org,
    actor,
    fake_info,
    app,
    env,
    source,
    permission_resolver,
    temporal_recorder,
    mode,
    fields,
    expected_tag,
    expected_sha,
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    app.build_mode = mode
    app.manifest_raw = MANIFEST
    app.save()
    result = _start(org, actor, fake_info, app, env, **fields)
    assert result["ok"], result
    dep = Deployment.objects.get(guid=result["data"]["id"])
    assert (dep.image_tag, dep.commit_sha) == (expected_tag, expected_sha)
    assert len(source[1]) == (1 if "sourceRef" in fields else 0)
    inp = temporal_recorder.starts[0][1][0]
    assert inp.commit_sha == expected_sha
    assert inp.image_tags == ({"app": expected_tag} if expected_tag else {})
    if mode == "none":
        from core.app_deploy import render_resources_for_deployment

        rendered = render_resources_for_deployment(dep)
        workload = next(r for r in rendered if r["kind"] == "Deployment")
        assert workload["spec"]["template"]["spec"]["containers"][0]["image"] == "nginx:1.27.5"


@pytest.mark.parametrize("mode", ["platform_build", "none"])
def test_approval_preserves_source_resolved_at_request(
    org,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    app,
    env_requires_approval,
    source,
    permission_resolver,
    temporal_recorder,
    mode,
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_APPROVE_DEPLOY)
    app.build_mode = mode
    app.save()
    result = _start(org, actor, fake_info, app, env_requires_approval)
    assert result["ok"], result
    assert result["data"]["status"] == "pending_approval"
    assert temporal_recorder.starts == []
    app.deploy_branch = "moved-branch"
    app.save()
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=other_actor.pk)):
        approved = LifecycleMutation().approve_deployment(
            fake_info_other, input=DeploymentByIdInput(id=result["data"]["id"])
        )
    assert approved.ok, approved.errors
    inp = temporal_recorder.starts[0][1][0]
    assert inp.commit_sha == (SHA if mode == "platform_build" else "")
    assert inp.image_tags == ({"app": SHA} if mode == "platform_build" else {})
    assert len(source[1]) == (1 if mode == "platform_build" else 0)


@pytest.mark.parametrize(
    "failure,code",
    [
        ("ci_pushed", "VALIDATION"),
        ("no_strategy", "PRECONDITION"),
        ("no_connection", "PRECONDITION"),
        ("foreign_connection", "PRECONDITION"),
        ("invalid_ref", "PRECONDITION"),
        ("conflicting_refs", "VALIDATION"),
        ("no_permission", "PERMISSION_DENIED"),
        ("foreign_tenant", "NOT_FOUND"),
    ],
)
def test_invalid_requests_do_not_create_or_supersede_work(
    org,
    actor,
    fake_info,
    app,
    env,
    source,
    permission_resolver,
    temporal_recorder,
    failure,
    code,
    monkeypatch,
):
    from astrolift_identity.models import Organization

    if failure != "no_permission":
        permission_resolver.grant(Permission.APP_DEPLOY)
    fields = {}
    if failure == "ci_pushed":
        app.build_mode = "ci_pushed"
        app.save()
    elif failure == "no_strategy":
        app.build_strategy = "off"
        app.save()
    elif failure == "no_connection":
        source[0].is_active = False
        source[0].save()
    elif failure == "foreign_connection":
        source[0].organization = Organization.objects.create(name="Foreign", slug="foreign")
        source[0].save()
    elif failure == "invalid_ref":
        monkeypatch.setattr(
            revisions.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b'{"sha":"main"}')
        )
    elif failure == "conflicting_refs":
        fields = {"sourceRef": "main", "commitSha": SHA}
    elif failure == "foreign_tenant":
        org = Organization.objects.create(name="Foreign", slug="foreign")
    existing = Deployment.objects.create(
        registered_app=app, app_environment=env, image_tag="v1", status="pending"
    )
    result = _start(org, actor, fake_info, app, env, **fields)
    assert not result["ok"], result
    assert result["errors"][0]["code"] == code
    assert Deployment.objects.filter(registered_app=app).count() == 1
    existing.refresh_from_db()
    assert existing.status == "pending"
    assert temporal_recorder.starts == temporal_recorder.terminates == []
    assert source[1] == []


def test_manifest_fetch_and_build_use_recorded_commit(
    org, actor, fake_info, app, env, source, permission_resolver, temporal_recorder, monkeypatch
):
    from astrolift_registry.services import manifest_sync
    from astrolift_workflows.activities.app_lifecycle import _resync_manifest_for_deploy_sync
    from astrolift_workflows.activities.build_image import BuildImageInput, _build_image_sync, _PreparedBuild
    from providers._sdk.build import BuildResult

    permission_resolver.grant(Permission.APP_DEPLOY)
    result = _start(org, actor, fake_info, app, env)
    assert result["ok"], result
    dep = Deployment.objects.get(guid=result["data"]["id"])
    app.deploy_branch = "moved-branch"
    app.default_branch = "also-moved"
    app.save()
    refs = []

    def fetch(connection, repo, path, ref):
        refs.append(ref)
        return MANIFEST

    monkeypatch.setattr(manifest_sync, "_default_fetch", fetch)
    outcome = _resync_manifest_for_deploy_sync(dep.pk)
    assert outcome["status"] in ("applied", "in_sync"), outcome
    assert refs == [SHA]
    builds = []

    class Driver:
        def build(self, spec, repo, tag):
            builds.append((spec.source_uri, tag))
            return BuildResult(
                success=True, image_uri=f"{repo}:{tag}", digest="sha256:" + "aa" * 32, duration_seconds=1
            )

    module = importlib.import_module("astrolift_workflows.activities.build_image")
    prepared = _PreparedBuild(
        driver=Driver(), registry_driver=None, repo_name="acme/web", repo_uri="registry.example/acme/web"
    )
    monkeypatch.setattr(module, "_prepare_build", lambda *a, **k: prepared)
    inp = temporal_recorder.starts[0][1][0]
    _build_image_sync(
        BuildImageInput(deployment_id=dep.pk, image_tag=inp.image_tags["app"], commit_sha=inp.commit_sha)
    )
    assert builds == [("git+https://github.com/acme/web#" + SHA, SHA)]
