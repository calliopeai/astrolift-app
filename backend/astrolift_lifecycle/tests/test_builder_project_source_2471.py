"""Ordinary Builder apps in an exact project, no brain (#2471).

Real PostgreSQL and the full HTTP/auth middleware stack with real API tokens and
RoleBindings. The native SDK doubles from the #2456 module stand in for the
cluster and registry; nothing contacts a cloud.
"""

import datetime as dt
import json
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from astrolift_identity.models import Organization, OrganizationModule, Project, Role, RoleBinding, Team
from astrolift_lifecycle import builder_brain_source as source
from astrolift_lifecycle.models import BuilderBrainBuild, Deployment
from astrolift_lifecycle.tests import test_builder_brain_source_2456 as fixtures
from astrolift_registry.models import BrainAppBinding, RegisteredApp
from core.permissions import Permission

org = fixtures.org
team = fixtures.team
user = fixtures.user
provider_plugin = fixtures.provider_plugin
cluster = fixtures.cluster
api_token = fixtures.api_token
auth_headers = fixtures.auth_headers
world = fixtures.world
pytestmark = pytest.mark.django_db

PROJECT_SOURCE = {
    "protocol": "builder-project-source-v1",
    "phases": ["build", "host"],
    "requires_project_target": True,
    "requires_brain": False,
    "providers": ["aws"],
    "runtime_verified": False,
}


def payload(world, **overrides):
    body = fixtures.payload(world, **overrides)
    del body["brain_slug"]
    return body


def create(world, body=None):
    return Client().post(
        f"/api/builder/v1/dev-environments/{world.dev.guid}/project-source-builds/",
        data=json.dumps(payload(world) if body is None else body),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + world.bearer,
    )


def ready(world, body=None):
    reply = create(world, body)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get(guid=reply.json()["id"])
    source.build(row.pk)
    row.refresh_from_db()
    assert row.status == "artifact_ready", row.failure_code
    return row


def host(world, row, **overrides):
    return Client().post(
        f"/api/builder/v1/project-source-builds/{row.guid}/host/",
        data=json.dumps({"expected_image_digest": fixtures.DIGEST, **overrides}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + world.bearer,
    )


def assert_nothing_admitted(world):
    assert not BuilderBrainBuild.objects.exists()
    assert not world.native.applied
    assert not world.starts


def assert_not_hosted(world, row):
    row.refresh_from_db()
    assert row.status == "artifact_ready"
    assert row.hosted_app_id is None and row.deployment_id is None
    assert not RegisteredApp.objects.filter(organization=world.org, slug="source-app").exists()
    assert not Deployment.objects.filter(registered_app__slug="source-app").exists()
    assert not BrainAppBinding.objects.exists()


@pytest.fixture
def audits():
    from core import mutations

    original = mutations._audit_writer
    rows = []
    mutations.register_audit_writer(rows.append)
    yield rows
    mutations.register_audit_writer(original)


def _other_team_project(world, slug="other"):
    other_team = Team.objects.create(organization=world.org, name="Other team", slug=f"{slug}-team")
    project = Project.objects.create(
        organization=world.org,
        team=other_team,
        name="Other project",
        slug=f"{slug}-project",
        default_tenant_cluster=world.cluster,
    )
    return other_team, project


def _grant(user, org, permissions, kind, scope_id, slug):
    role = Role.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        scope_level=getattr(Role.ScopeLevel, kind),
        permissions=[p.value for p in permissions],
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id)


# -- Discovery -----------------------------------------------------------------


def test_capabilities_advertise_project_source_and_keep_brain_source(world):
    client = Client(HTTP_AUTHORIZATION="Bearer " + world.bearer)
    caps = client.get("/api/builder/v1/capabilities/").json()
    assert caps["project_source"] == PROJECT_SOURCE
    assert caps["brain_source"] == {
        "protocol": "builder-brain-source-v1",
        "phases": ["build", "host"],
        "requires_project_target": True,
        "providers": ["aws"],
        "runtime_verified": False,
    }
    project = client.get(f"/api/builder/v1/dev-environments/{world.dev.guid}/project-source/")
    brain = client.get(f"/api/builder/v1/dev-environments/{world.dev.guid}/source/")
    assert project.status_code == brain.status_code == 200
    assert project.json() == {**brain.json(), "protocol": "builder-project-source-v1"}
    assert brain.json()["protocol"] == "builder-brain-source-v1"
    assert not world.native.applied and not BuilderBrainBuild.objects.exists()


# -- Build, then fresh host ----------------------------------------------------


def test_project_source_hosts_an_ordinary_app_without_reading_a_brain(world, monkeypatch, audits):
    from astrolift_registry import brain_apps

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary project hosting reached a brain")

    world.brain.soft_delete(by=world.user)
    for name in ("readable_brain_for", "mcp_urls", "bind"):
        monkeypatch.setattr(brain_apps, name, forbidden)

    row = ready(world)
    assert row.hosting_mode == "project" and row.brain_id is None
    assert row.hosted_app_id is None and row.deployment_id is None
    assert not RegisteredApp.objects.filter(slug="source-app").exists()
    assert not world.native.objects
    grant = json.loads(next(r for r in world.native.applied if r["kind"] == "Secret")["stringData"]["config"])
    assert grant["url"].endswith(f"/api/builder/v1/project-source-builds/{row.guid}/archive/")

    hosted_at = timezone.now()
    reply = host(world, row)
    assert reply.status_code == 202, reply.content
    data = reply.json()
    assert data["protocol"] == "builder-project-source-v1"
    assert data["status"] == "hosted" and data["hosting_status"] == "created"
    assert data["project_guid"] == str(world.project.guid)
    assert data["brain_guid"] is None and data["brain_slug"] is None
    assert data["deployment_status"] == "pending" and data["runtime_verified"] is False
    assert data["data_persistent"] is False and data["delete_data_on_expiry"] is False

    row.refresh_from_db()
    app, deployment = row.hosted_app, row.deployment
    assert data["app_guid"] == str(app.guid) and data["deployment_guid"] == str(deployment.guid)
    assert app.project_id == world.project.pk and app.team_id == world.team.pk
    assert app.default_tenant_cluster_id == world.cluster.pk and app.created_by_id == world.user.pk
    # A relative expiry starts when the app is created, under the policy then.
    assert hosted_at + dt.timedelta(hours=4) <= app.expires_at <= timezone.now() + dt.timedelta(hours=4)
    assert data["expires_at"] == app.expires_at.isoformat()
    assert "[brain]" not in app.manifest_raw and 'kind = "empty_dir"' in app.manifest_raw
    assert not BrainAppBinding.objects.filter(registered_app=app).exists()
    assert deployment.image_digest == fixtures.DIGEST and deployment.triggered_by_user_id == world.user.pk
    builder = [a for a in audits if a.action.startswith("builder.")]
    assert [a.action for a in builder] == [
        "builder.project_source.admit",
        "builder.project_source.artifact_ready",
        "builder.project_source.host",
    ]
    assert all(
        a.extra == {"project": str(world.project.guid), "archive_sha256": row.archive_sha256} for a in builder
    )
    assert any(a.action == "app.expiry.set" and a.target_id == app.slug for a in audits)

    repeated = host(world, row)
    assert repeated.status_code == 202, repeated.content
    assert repeated.json()["app_guid"] == data["app_guid"]
    assert repeated.json()["deployment_guid"] == data["deployment_guid"]
    expires_at = app.expires_at
    app.refresh_from_db()
    assert app.expires_at == expires_at
    assert BuilderBrainBuild.objects.count() == 1
    assert RegisteredApp.objects.filter(organization=world.org, slug="source-app").count() == 1
    assert Deployment.objects.filter(registered_app=app).count() == 1


def test_deletion_preference_and_expiry_extension_use_the_existing_contract(world):
    from astrolift_registry.schema.mutations import RegistryMutation
    from astrolift_registry.schema.mutations.expiry import SetAppExpiryInput
    from core.tenancy import TenantContext, tenant_context
    from core.tests.utils.scope_world import make_info

    row = ready(world, payload(world, expires_in="1d", delete_data_on_expiry=True))
    reply = host(world, row)
    assert reply.status_code == 202, reply.content
    assert reply.json()["delete_data_on_expiry"] is True
    row.refresh_from_db()
    app = row.hosted_app
    assert app.delete_data_on_expiry is True
    role = Role.objects.get(organization=world.org, slug="builder")
    role.permissions += [Permission.APP_UPDATE.value]
    role.save()
    before = app.expires_at
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        extended = RegistryMutation().set_app_expiry(
            make_info(world.user), input=SetAppExpiryInput(app_slug=app.slug, extend_by="1d")
        )
    assert extended.ok, extended.errors
    app.refresh_from_db()
    assert app.expires_at == before + dt.timedelta(days=1)
    status = Client().get(
        f"/api/builder/v1/project-source-builds/{row.guid}/", HTTP_AUTHORIZATION="Bearer " + world.bearer
    )
    assert status.status_code == 200 and status.json()["expires_at"] == app.expires_at.isoformat()


def test_expired_project_app_tears_down_and_is_never_recreated(world, monkeypatch):
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

    from astrolift_registry import brain_apps, expiry
    from astrolift_registry.expiry_cleanup import retire_runtime
    from astrolift_registry.tests.test_expiry_cleanup_2433 import NativeObjects
    from astrolift_workflows.activities.app_teardown import _soft_delete_app_records_sync
    from core.app_deploy import render_resources_for_deployment

    row = ready(world)
    assert host(world, row).status_code == 202
    row.refresh_from_db()
    app, deployment = row.hosted_app, row.deployment
    namespace = app.k8s_namespace
    resources = render_resources_for_deployment(deployment, include_managed_filesystems=False)
    pod = next(item for item in resources if item["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"] == row.image_ref
    assert {v["name"] for v in pod["volumes"] if "emptyDir" in v} == {"scratch", "data"}
    assert "BRAIN_" not in json.dumps(resources)
    assert brain_apps.brain_secret(deployment, namespace) is None
    assert brain_apps.deployment_brain_secret(deployment) is None

    objects = NativeObjects()
    objects.put(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    "astrolift.io/managed-by": "astrolift",
                    "astrolift.io/organization": world.org.slug,
                    "astrolift.io/app": app.slug,
                },
                "annotations": {"astrolift.io/registered-app-id": str(app.pk)},
            },
        }
    )
    for item in resources:
        objects.put(item)
    driver = K8sNativeClusterDriver(config=K8sNativeConfig(), k8s_client_factory=lambda **kwargs: objects)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: driver)
    assert expiry.fire(app.pk, now=app.expires_at)
    retire_runtime(app.pk)
    summary = _soft_delete_app_records_sync(app.pk)
    assert summary["brain_bindings"] == 0
    again = host(world, row)
    assert again.status_code == 409, again.content
    assert again.json()["code"] == "HOSTED_APP_UNAVAILABLE"
    assert RegisteredApp.objects.filter(organization=world.org, slug="source-app").count() == 0
    assert BuilderBrainBuild.objects.count() == 1


# -- Authorization ---------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["other_org", "unknown_project", "deleted_project", "token_team_ceiling", "missing_write_scope"],
)
def test_destination_and_bearer_refuse_before_any_effect(world, case):
    body = payload(world)
    if case == "other_org":
        other = Organization.objects.create(name="Other org", slug="project-source-other-org")
        OrganizationModule.objects.create(
            organization=other, key=OrganizationModule.Key.CHAT_STUDIO_INTEGRATION, enabled=True
        )
        other_team = Team.objects.create(organization=other, name="Other", slug="other")
        project = Project.objects.create(organization=other, team=other_team, name="Other", slug="other")
        body["project_guid"] = str(project.guid)
    elif case == "unknown_project":
        body["project_guid"] = str(uuid4())
    elif case == "deleted_project":
        world.project.soft_delete(by=world.user)
    elif case == "token_team_ceiling":
        # The bearer covers the source's team, never another team's project.
        _, project = _other_team_project(world)
        body["project_guid"] = str(project.guid)
        world.token.team = world.team
        world.token.save()
    else:
        world.token.scopes = ["read:apps"]
        world.token.save()
    reply = create(world, body)
    expected = 404 if case in ("other_org", "unknown_project", "deleted_project") else 403
    assert reply.status_code == expected, reply.content
    assert_nothing_admitted(world)


@pytest.mark.parametrize("grant", ["none", "create_only", "deploy_only", "project_create_and_deploy"])
def test_destination_needs_app_create_and_app_deploy_on_the_exact_project(world, settings, grant):
    # Without org-wide grants the caller keeps only its source team's role.
    RoleBinding.objects.filter(user=world.user, scope_kind="ORG").delete()
    other_team, project = _other_team_project(world)
    settings.BUILDER_BRAIN_BUILD_TARGETS = {str(project.guid): world.target}
    if grant == "create_only":
        _grant(world.user, world.org, [Permission.APP_CREATE], "TEAM", other_team.pk, "create-only")
    elif grant == "deploy_only":
        _grant(world.user, world.org, [Permission.APP_DEPLOY], "TEAM", other_team.pk, "deploy-only")
    elif grant == "project_create_and_deploy":
        _grant(
            world.user,
            world.org,
            [Permission.APP_CREATE, Permission.APP_DEPLOY, Permission.APP_READ],
            "PROJECT",
            project.pk,
            "project-builder",
        )
    reply = create(world, payload(world, project_guid=str(project.guid)))
    if grant != "project_create_and_deploy":
        assert reply.status_code == 403, reply.content
        assert reply.json()["code"] == "PERMISSION_DENIED"
        assert_nothing_admitted(world)
        return
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    assert row.project_id == project.pk and row.hosting_mode == "project"
    source.build(row.pk)
    hosted = host(world, row)
    assert hosted.status_code == 202, hosted.content
    row.refresh_from_db()
    assert row.hosted_app.project_id == project.pk and row.hosted_app.team_id == other_team.pk


@pytest.mark.parametrize(
    "change",
    ["token", "scope", "create", "deploy", "source_owner", "project_team", "project_deleted", "module"],
)
def test_revoked_authority_refuses_host_without_effects(world, change):
    row = ready(world)
    if change == "token":
        world.token.is_revoked = True
        world.token.save()
    elif change == "scope":
        world.token.scopes = ["read:apps"]
        world.token.save()
    elif change in ("create", "deploy"):
        withdrawn = Permission.APP_CREATE if change == "create" else Permission.APP_DEPLOY
        role = Role.objects.get(organization=world.org, slug="builder")
        role.permissions = [p for p in role.permissions if p != withdrawn.value]
        role.save()
    elif change == "source_owner":
        from django.contrib.auth import get_user_model

        world.dev.creator = get_user_model().objects.create_user(username="project-source-new-owner")
        world.dev.save()
    elif change == "project_team":
        world.project.team = Team.objects.create(organization=world.org, name="Moved", slug="moved")
        world.project.save()
    elif change == "project_deleted":
        world.project.soft_delete(by=world.user)
    else:
        OrganizationModule.objects.filter(
            organization=world.org, key=OrganizationModule.Key.CHAT_STUDIO_INTEGRATION
        ).update(enabled=False)
    applied = len(world.native.applied)
    reply = host(world, row)
    assert reply.status_code in (401, 403, 404), reply.content
    assert_not_hosted(world, row)
    assert len(world.native.applied) == applied


def test_revoked_authority_refuses_the_background_build(world):
    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    role = Role.objects.get(organization=world.org, slug="builder")
    role.permissions = [p for p in role.permissions if p != Permission.APP_DEPLOY.value]
    role.save()
    with pytest.raises(RuntimeError):
        source.build(row.pk)
    row.refresh_from_db()
    assert row.status == "failed" and not row.image_digest
    assert not world.native.applied


def test_native_origin_guard_refuses_project_host(world, monkeypatch):
    row = ready(world)
    monkeypatch.setattr(
        "astrolift_lifecycle.deployment_identity_origin.native_origin_required", lambda *a, **k: True
    )
    reply = host(world, row)
    assert reply.status_code == 403, reply.content
    assert reply.json()["code"] == "DEPLOY_ADMISSION_REFUSED"
    assert_not_hosted(world, row)


# -- Temporary expiry policy -----------------------------------------------------


@pytest.mark.parametrize(
    "policy,overrides,code",
    [
        ({"ttl_required_surfaces": ["chat_studio"]}, {"expires_in": None}, "EXPIRY_REQUIRED"),
        ({"temporary_only_for_members": True}, {"expires_in": None}, "EXPIRY_REQUIRED"),
        ({"max_ttl_seconds": 3600}, {"expires_in": "4h"}, "VALIDATION"),
        ({}, {"expires_in": "4h", "expires_at": "2099-01-01T00:00:00+00:00"}, "VALIDATION"),
    ],
)
def test_admission_refuses_an_expiry_outside_current_policy(world, policy, overrides, code):
    world.org.temporary_app_policy = policy
    world.org.save()
    body = payload(world, **overrides)
    if body.get("expires_in") is None:
        del body["expires_in"]
    reply = create(world, body)
    assert reply.status_code == 400, reply.content
    assert reply.json()["code"] == code
    assert_nothing_admitted(world)


@pytest.mark.parametrize(
    "policy,overrides",
    [
        ({"max_ttl_seconds": 3600}, {}),
        ({"ttl_required_surfaces": ["chat_studio"]}, {"expires_in": None}),
    ],
)
def test_host_applies_the_policy_current_at_app_creation(world, policy, overrides):
    body = payload(world, **overrides)
    if body.get("expires_in") is None:
        del body["expires_in"]
    row = ready(world, body)
    world.org.temporary_app_policy = policy
    world.org.save()
    reply = host(world, row)
    assert reply.status_code == 403, reply.content
    assert reply.json()["code"] == "HOST_ADMISSION_REFUSED"
    assert_not_hosted(world, row)
    # The same verified artifact hosts once the policy admits the request.
    world.org.temporary_app_policy = {}
    world.org.save()
    hosted = host(world, row)
    assert hosted.status_code == 202, hosted.content
    row.refresh_from_db()
    assert (row.hosted_app.expires_at is None) == ("expires_in" not in body)


# -- Idempotency, closed intents and route families ------------------------------


def test_identical_replay_is_one_intent_and_any_change_conflicts(world):
    first = create(world)
    assert first.status_code == 202, first.content
    again = create(world)
    assert again.status_code == 202 and again.json()["id"] == first.json()["id"]
    for changed in (payload(world, expires_in="5h"), payload(world, app_slug="other-app")):
        conflict = create(world, changed)
        assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    # The key namespace is shared with brain intents.
    brain = fixtures.create(world, fixtures.payload(world))
    assert brain.status_code == 409 and brain.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert BuilderBrainBuild.objects.count() == 1 and not world.native.applied


def test_failed_build_is_never_rebuilt_or_hosted_by_replay(world):
    assert create(world).status_code == 202
    row = BuilderBrainBuild.objects.get()
    world.native.fail = True
    with pytest.raises(RuntimeError, match="BUILD_FAILED"):
        source.build(row.pk)
    replay = create(world)
    assert replay.status_code == 202, replay.content
    assert replay.json()["id"] == str(row.guid) and replay.json()["status"] == "failed"
    refused = host(world, row)
    assert refused.status_code == 409 and refused.json()["code"] == "ARTIFACT_NOT_READY"
    assert BuilderBrainBuild.objects.count() == 1
    assert not RegisteredApp.objects.filter(organization=world.org, slug="source-app").exists()


@pytest.mark.parametrize("field", ["brain_slug", "expected_brain_guid", "project_slug", "team_slug", "image"])
def test_project_request_refuses_brain_fields_and_aliases(world, field):
    reply = create(world, {**payload(world), field: "untrusted"})
    assert reply.status_code == 400, reply.content
    assert_nothing_admitted(world)


def test_route_families_never_serve_each_others_intents(world):
    project_row = ready(world)
    brain_reply = fixtures.create(world, fixtures.payload(world, idempotency_key="brain-intent"))
    assert brain_reply.status_code == 202, brain_reply.content
    brain_row = BuilderBrainBuild.objects.get(guid=brain_reply.json()["id"])
    assert brain_row.hosting_mode == "brain" and brain_row.brain_id == world.brain.pk
    client = Client(HTTP_AUTHORIZATION="Bearer " + world.bearer)
    assert client.get(f"/api/builder/v1/brain-source-builds/{project_row.guid}/").status_code == 404
    assert fixtures.host(world, project_row).status_code == 404
    assert client.get(f"/api/builder/v1/project-source-builds/{brain_row.guid}/").status_code == 404
    assert host(world, brain_row).status_code == 404
    assert_not_hosted(world, project_row)
    # A build grant opens only its own family's archive route.
    from django.core import signing

    grant = signing.dumps(
        {"intent": str(project_row.guid), "org": world.org.pk, "sha256": project_row.archive_sha256},
        salt=source.ARCHIVE_SALT,
    )
    BuilderBrainBuild.objects.filter(pk=project_row.pk).update(status="building")
    header = {"HTTP_AUTHORIZATION": "BuilderBuildArchive " + grant}
    assert (
        Client().get(f"/api/builder/v1/brain-source-builds/{project_row.guid}/archive/", **header).status_code
        == 403
    )
    served = Client().get(f"/api/builder/v1/project-source-builds/{project_row.guid}/archive/", **header)
    assert served.status_code == 200 and served["Cache-Control"] == "private, no-store"
    served.close()


def test_brain_route_still_requires_its_brain(world):
    body = fixtures.payload(world)
    del body["brain_slug"]
    reply = fixtures.create(world, body)
    assert reply.status_code == 400, reply.content
    assert_nothing_admitted(world)


def test_database_keeps_hosting_mode_and_brain_coherent(world):
    row = ready(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        BuilderBrainBuild.objects.filter(pk=row.pk).update(hosting_mode="brain")
    with pytest.raises(IntegrityError), transaction.atomic():
        BuilderBrainBuild.objects.filter(pk=row.pk).update(brain=world.brain)
    row.refresh_from_db()
    assert row.hosting_mode == "project" and row.brain_id is None
