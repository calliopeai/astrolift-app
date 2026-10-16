"""Apps hosted on a brain (#2243): created from a brain the caller can read,
given the brain's MCP URLs and a read token at deploy, rotated per deploy and
revoked with the app."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import jwt
import pytest

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_registry import brain_apps, brain_tokens
from astrolift_registry.models import BrainAppBinding, RegisteredApp
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.brains import HostAppOnBrainInput
from astrolift_workflows.activities.app_lifecycle import _update_secrets_sync
from core.app_deploy import deployment_env_from, render_resources_for_deployment
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user
from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

pytestmark = pytest.mark.django_db

ISSUER = "https://astrolift.example.net"
BRAIN_URL = "https://brain.acme.example"


@pytest.fixture(autouse=True)
def _env(monkeypatch, settings):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr(
        "astrolift_registry.schema.mutations.registration._bootstrap_app_environments", lambda *a, **k: None
    )
    settings.APP_BASE_URL = ISSUER


@pytest.fixture
def world():
    from astrolift_clusters.models import TenantCluster

    world = ScopeWorld("ba2243")
    world.user = make_user("ba2243")
    world.cluster = make_cluster(world, "ba2243")
    TenantCluster.objects.filter(pk=world.cluster.pk).update(lifecycle=TenantCluster.Lifecycle.MANAGED.value)
    RegisteredApp.objects.filter(pk__in=[world.medops_app.pk, world.platform_app.pk]).update(
        app_kind="mimwell"
    )
    world.medops_app.refresh_from_db()
    AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="production", url=BRAIN_URL
    )
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_CREATE],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="builder",
    )
    return world


def _host(world, brain_slug, **extra):
    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    ):
        return RegistryMutation().host_app_on_brain(
            make_info(world.user),
            input=HostAppOnBrainInput(
                brain_slug=brain_slug,
                project_id=str(world.medops_project.guid),
                image="ghcr.io/acme/brain-chat:1.0",
                **extra,
            ),
        )


def _hosted(world):
    result = _host(world, world.medops_app.slug, slug="brain-chat")
    assert result.ok, result.errors
    app = RegisteredApp.objects.get(organization=world.org, slug="brain-chat")
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=world.cluster, name="production")
    return app, env


def _deploy(app, env, user=None):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="1.0",
        triggered_by_user=user,
    )


class _Driver:
    def __init__(self):
        self.applied: list[dict] = []

    def apply_manifests(self, cluster_slug, namespace, manifests, dry_run=False):
        from providers._sdk.cluster import ApplyResult

        if not dry_run:
            self.applied = manifests
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])


def _materialize(deployment, monkeypatch) -> dict[str, str]:
    driver = _Driver()
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda d: (driver, SimpleNamespace(slug="c"), "acme-brain-chat"),
    )
    _update_secrets_sync(deployment.pk)
    [secret] = [m for m in driver.applied if "astrolift.io/brain-for" in m["metadata"]["labels"]]
    assert secret["metadata"]["name"] == brain_apps.brain_secret_name("brain-chat", "production")
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


def _verify(token, audience):
    keys = {k["kid"]: k for k in brain_tokens.jwks()["keys"]}
    kid = jwt.get_unverified_header(token)["kid"]
    if kid not in keys:
        raise jwt.InvalidKeyError(f"{kid} is not published")
    public = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(keys[kid]))
    return jwt.decode(token, public, algorithms=["ES256"], audience=audience, issuer=ISSUER)


def test_the_manifest_names_the_brain_it_reads():
    manifest = brain_apps.brain_app_manifest(slug="chat", brain_slug="acme-brain", image="ghcr.io/a/b:1")
    raw = parse_raw(manifest)
    assert raw.brain == "acme-brain"
    assert normalize(raw).serialized["brain"] == "acme-brain"
    assert "brain" not in normalize(parse_raw('name = "x"\n')).serialized
    for bad in ('[brain]\nslug = ""\n', "brain = 1\n", '[brain]\nslug = "a"\nwrite = true\n'):
        with pytest.raises(ManifestError):
            parse_raw('name = "x"\n' + bad)


def test_hosting_an_app_creates_it_from_the_template_and_binds_it(world):
    result = _host(world, world.medops_app.slug, slug="brain-chat", port=3000)

    assert result.ok, result.errors
    assert result.data.template == "brain-app" and result.data.brain_slug == world.medops_app.slug
    assert set(result.data.env_keys) == set(brain_apps.ENV_KEYS)
    app = RegisteredApp.objects.get(organization=world.org, slug="brain-chat")
    assert app.project_id == world.medops_project.pk and app.build_mode == RegisteredApp.BuildMode.NONE
    assert parse_raw(app.manifest_raw).brain == world.medops_app.slug
    assert parse_raw(app.manifest_raw).workloads[0].containers[0].port == 3000
    binding = BrainAppBinding.objects.get(registered_app=app)
    assert binding.brain_id == world.medops_app.pk and binding.granted_by_id == world.user.pk


def test_a_brain_the_caller_cannot_read_or_that_is_not_served_hosts_nothing(world):
    assert _host(world, world.platform_app.slug).errors[0].code == "NOT_FOUND"
    AppEnvironment.objects.filter(registered_app=world.medops_app).update(url="")
    assert _host(world, world.medops_app.slug).errors[0].code == "PRECONDITION"
    assert _host(world, "", slug="x").errors[0].code == "NOT_FOUND"
    assert not BrainAppBinding.objects.exists()
    assert not RegisteredApp.objects.filter(organization=world.org).exclude(
        pk__in=[world.medops_app.pk, world.platform_app.pk]
    )


def test_a_deploy_gives_the_app_the_brain_and_a_token_the_edge_admits_to_it_only(world, monkeypatch):
    app, env = _hosted(world)
    deployment = _deploy(app, env)

    values = _materialize(deployment, monkeypatch)

    assert values["BRAIN_SLUG"] == world.medops_app.slug
    assert values["BRAIN_MCP_URL"] == values["BRAIN_MCP_URLS"] == f"{BRAIN_URL}/mcp"
    assert values["BRAIN_TOKEN_HEADER"] == BRAIN_TOKEN_HEADER
    claims = _verify(values["BRAIN_TOKEN"], str(world.medops_app.guid))
    assert claims["sub"] == f"app:{app.guid}" and claims["scope"] == "brain:read"
    with pytest.raises(jwt.InvalidAudienceError):
        _verify(values["BRAIN_TOKEN"], str(world.platform_app.guid))

    env_from, _ = deployment_env_from(deployment)
    assert brain_apps.brain_secret_name(app.slug, env.name) in env_from
    rendered = render_resources_for_deployment(deployment)
    web = next(r for r in rendered if r["kind"] == "Deployment")
    kid = jwt.get_unverified_header(values["BRAIN_TOKEN"])["kid"]
    assert web["spec"]["template"]["metadata"]["annotations"][brain_apps.KEY_ANNOTATION] == kid
    refs = [ref["secretRef"]["name"] for ref in web["spec"]["template"]["spec"]["containers"][0]["envFrom"]]
    assert brain_apps.brain_secret_name(app.slug, env.name) in refs


def test_a_redeploy_rotates_the_token_and_the_one_before_last_stops_verifying(world, monkeypatch):
    app, env = _hosted(world)
    audience = str(world.medops_app.guid)
    first_deploy = _deploy(app, env)
    first = _materialize(first_deploy, monkeypatch)["BRAIN_TOKEN"]
    # A retried deploy step keeps its key.
    again = _materialize(first_deploy, monkeypatch)["BRAIN_TOKEN"]
    assert jwt.get_unverified_header(again)["kid"] == jwt.get_unverified_header(first)["kid"]

    second = _materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"]
    assert jwt.get_unverified_header(second)["kid"] != jwt.get_unverified_header(first)["kid"]
    _verify(first, audience)  # the release being replaced still reads

    third = _materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"]
    _verify(second, audience)
    _verify(third, audience)
    with pytest.raises(jwt.InvalidKeyError):
        _verify(first, audience)


def test_deleting_the_app_or_the_brain_revokes_its_tokens(world, monkeypatch):
    app, env = _hosted(world)
    token = _materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"]
    audience = str(world.medops_app.guid)

    RegisteredApp.objects.filter(pk=world.medops_app.pk).update(archived_at=world.medops_app.created_at)
    with pytest.raises(jwt.InvalidKeyError):
        _verify(token, audience)
    RegisteredApp.objects.filter(pk=world.medops_app.pk).update(archived_at=None)
    _verify(token, audience)

    from astrolift_workflows.activities.app_teardown import _soft_delete_app_records_sync

    assert _soft_delete_app_records_sync(app.pk)["brain_bindings"] == 1
    with pytest.raises(jwt.InvalidKeyError):
        _verify(token, audience)
    assert not BrainAppBinding.objects.filter(registered_app=app).exists()


def test_a_deploy_refuses_a_brain_the_app_is_not_bound_to_or_the_deployer_cannot_read(world, monkeypatch):
    from core.app_deploy import AppDeployError

    app, env = _hosted(world)
    outsider = make_user("ba2243-out")
    with pytest.raises(AppDeployError, match="can no longer read"):
        _materialize(_deploy(app, env, user=outsider), monkeypatch)

    app.manifest_raw = app.manifest_raw.replace(world.medops_app.slug, world.platform_app.slug)
    app.save(update_fields=["manifest_raw"])
    with pytest.raises(AppDeployError, match="not bound"):
        _materialize(_deploy(app, env), monkeypatch)
    assert deployment_env_from(_deploy(app, env))[0] == []


def test_caller_tokens_never_sign_with_a_binding_key(world, monkeypatch):
    app, env = _hosted(world)
    _materialize(_deploy(app, env), monkeypatch)
    assert brain_tokens.signing_key().binding_id is None
