"""Real PostgreSQL + HTTP auth; native SDK doubles never contact a cloud."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import Client

from astrolift_identity.models import Project, Role
from astrolift_lifecycle import builder_brain_source as source
from astrolift_lifecycle.models import AppEnvironment, BuilderBrainBuild
from astrolift_lifecycle.tests import test_builder_api as fixtures
from astrolift_registry.models import BrainAppBinding, RegisteredApp

org = fixtures.org
team = fixtures.team
user = fixtures.user
provider_plugin = fixtures.provider_plugin
cluster = fixtures.cluster
api_token = fixtures.api_token
auth_headers = fixtures.auth_headers
pytestmark = pytest.mark.django_db
DIGEST = "sha256:" + "a" * 64


class Registry:
    def __init__(self, target):
        self.target = target
        self.tags = []
        self.immutable = True

    def observe_build_repository(self, name):
        from _sdk.registry import BuildRepositoryObservation

        return BuildRepositoryObservation(name, self.target["registry_repo_uri"], self.immutable)

    def list_tags(self, name):
        assert name == self.target["registry_repo_name"]
        return self.tags


class Native:
    def __init__(self, target, registry):
        self.target, self.registry = target, registry
        self.objects, self.applied, self.deleted = {}, [], []
        self.fail = False
        self.push = True
        self.delete_failure = False
        self.apply_hook = None
        self.completion_hook = None

    def get_manifest(self, cluster, namespace, kind, name):
        target = self.target
        if kind == "Namespace":
            return {"metadata": {"name": name, "uid": target["namespace_uid"]}}
        if kind == "ServiceAccount":
            return {
                "metadata": {
                    "name": name,
                    "uid": target["service_account_uid"],
                    "annotations": {"eks.amazonaws.com/role-arn": target["role_arn"]},
                }
            }
        return copy.deepcopy(self.objects.get((kind, name)))

    def apply_manifests(self, cluster, namespace, manifests, *, create_only=False):
        from _sdk.cluster import ApplyResult

        assert create_only is True
        assert namespace == self.target["namespace"]
        created = []
        for row in copy.deepcopy(manifests):
            assert row["kind"] in ("Job", "Secret")
            key = row["kind"], row["metadata"]["name"]
            assert key not in self.objects
            row["metadata"]["uid"] = str(uuid4())
            self.objects[key] = row
            created.append("/".join(key))
        self.applied.extend(copy.deepcopy(manifests))
        if self.apply_hook:
            self.apply_hook(manifests)
        return ApplyResult(created=created, updated=[], unchanged=[], errors=[])

    def get_workload_status(self, cluster, namespace, kind, name):
        from _sdk.registry import Tag

        job = self.objects[("Job", name)]
        job["status"] = {"succeeded": 0 if self.fail else 1}
        if not self.fail and self.push:
            args = job["spec"]["template"]["spec"]["containers"][0]["args"]
            tag = next(a for a in args if a.startswith("--destination=")).rsplit(":", 1)[1]
            self.registry.tags = [Tag(tag, DIGEST, None)]
        if self.completion_hook:
            self.completion_hook(job)
        return SimpleNamespace(
            conditions=[
                {
                    "type": "Failed" if self.fail else "Complete",
                    "status": "True",
                    "message": "synthetic native job result",
                }
            ]
        )

    def delete_manifests(self, cluster, namespace, manifests):
        from _sdk.cluster import DeleteResult

        if self.delete_failure:
            return DeleteResult(deleted=[], not_found=[], errors=["synthetic deletion refusal"])
        for row in manifests:
            key = row["kind"], row["metadata"]["name"]
            assert row["metadata"]["uid"] == self.objects[key]["metadata"]["uid"]
            self.deleted.append(copy.deepcopy(row))
            del self.objects[key]
        return DeleteResult(deleted=["/".join(key)], not_found=[], errors=[])


@pytest.fixture
def world(org, team, user, cluster, api_token, settings, monkeypatch, tmp_path):
    from astrolift_clusters.models import ProviderPlugin
    from astrolift_workflows import client as workflow_client
    from core.permissions import Permission

    original_once = workflow_client.start_workflow_once
    original_recover = workflow_client.recover_workflow_once
    original_describe = workflow_client.describe_workflow_instance
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.APP_BASE_URL = "https://astrolift.synthetic.invalid"
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)

    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("astrolift_scm.services.autowire.run_autowire", lambda *a, **k: None)
    role = Role.objects.get(slug="builder", organization=org)
    role.permissions += [Permission.APP_READ.value]
    role.save()
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "AWS", "plugin_version": "1", "config_schema": {}, "capabilities_manifest": {}},
    )
    cluster.provider_plugin = plugin
    cluster.provider_config = {"region": "us-east-1", "account_id": "123456789012"}
    cluster.auth_config = {"cluster_oidc_issuer": "https://oidc.eks.synthetic.invalid/id/test"}
    cluster.save()
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Source destination",
        slug="source-destination",
        default_tenant_cluster=cluster,
    )
    brain = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Brain",
        slug="existing-brain",
        app_kind="mimwell",
        build_mode="none",
    )
    AppEnvironment.objects.create(
        registered_app=brain, name="production", tenant_cluster=cluster, url="https://brain.synthetic.invalid"
    )
    dev = fixtures._running_dev_env(org, cluster, user)
    dev.team = team
    dev.start_command = "python main.py"
    dev.files = {"main.py": "print('source')", "requirements.txt": "flask==3.1.0"}
    dev.data_file_path, dev.data_file = "nested/seed.sqlite", fixtures._sqlite_bytes(tmp_path, 3, 16)
    dev.save()
    target = {
        "organization_guid": str(org.guid),
        "cluster_guid": str(cluster.guid),
        "namespace": "build-source-project",
        "namespace_uid": "namespace-original",
        "service_account": "build-source-project",
        "service_account_uid": "sa-original",
        "role_arn": "arn:aws:iam::123456789012:role/source-project-push-only",
        "registry_repo_name": "source-project",
        "registry_repo_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/source-project",
        "attested_project_isolation": True,
    }
    settings.BUILDER_BRAIN_BUILD_TARGETS = {str(project.guid): target}
    registry = Registry(target)
    native = Native(target, registry)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: native)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug=cluster.slug)
    )
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: registry)
    starts = []

    def start(name, args, **kwargs):
        from astrolift_workflows.client import WorkflowHandle

        starts.append((name, args, kwargs))
        return WorkflowHandle(kwargs["workflow_id"], "synthetic-run", True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)
    executions = {}

    def once(name, args, **kwargs):
        from astrolift_workflows.client import WorkflowHandle

        workflow_id = kwargs["workflow_id"]
        if workflow_id not in executions:
            starts.append((name, args, kwargs))
            executions[workflow_id] = (name, args, WorkflowHandle(workflow_id, str(uuid4()), True))
        expected, accepted, handle = executions[workflow_id]
        if expected != name or accepted != args:
            raise RuntimeError("synthetic exact engine payload mismatch")
        return handle

    def recover(name, args, **kwargs):
        if kwargs["workflow_id"] in executions:
            return once(name, args, **kwargs)
        return None

    monkeypatch.setattr("astrolift_workflows.client.start_workflow_once", once)
    monkeypatch.setattr("astrolift_workflows.client.recover_workflow_once", recover)
    monkeypatch.setattr("astrolift_workflows.client.describe_workflow_instance", lambda *a, **k: None)
    return SimpleNamespace(
        org=org,
        team=team,
        user=user,
        cluster=cluster,
        project=project,
        brain=brain,
        dev=dev,
        target=target,
        registry=registry,
        native=native,
        starts=starts,
        token=api_token[0],
        bearer=api_token[1],
        original_once=original_once,
        original_recover=original_recover,
        original_describe=original_describe,
    )


def payload(world, **overrides):
    digest = source.source_descriptor(world.dev)[0]["archive_sha256"]
    return {
        "project_guid": str(world.project.guid),
        "brain_slug": world.brain.slug,
        "app_name": "Source app",
        "app_slug": "source-app",
        "idempotency_key": "source-build-1",
        "expected_archive_sha256": digest,
        "expires_in": "4h",
        "delete_data_on_expiry": False,
        **overrides,
    }


def create(world, body=None):
    return Client().post(
        f"/api/builder/v1/dev-environments/{world.dev.guid}/brain-source-builds/",
        data=json.dumps(body or payload(world)),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + world.bearer,
    )


def ready(world):
    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get(guid=reply.json()["id"])
    source.build(row.pk)
    row.refresh_from_db()
    return row


def host(world, row, **overrides):
    return Client().post(
        f"/api/builder/v1/brain-source-builds/{row.guid}/host/",
        data=json.dumps({"expected_image_digest": DIGEST, **overrides}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + world.bearer,
    )


def test_authorized_snapshot_real_kaniko_and_fresh_host(world):
    row = ready(world)
    assert row.status == "artifact_ready" and row.image_ref.endswith("@" + DIGEST)
    assert not row.hosted_app_id and not BrainAppBinding.objects.exists()
    assert not world.native.objects
    assert {r["kind"] for r in world.native.deleted} == {"Job", "Secret"}
    job = next(r for r in world.native.applied if r["kind"] == "Job")
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "--context=dir:///workspace" in pod["containers"][0]["args"]
    assert all(m["name"] != "archive" for m in pod["containers"][0]["volumeMounts"])
    assert world.bearer not in json.dumps(row.admission)
    assert all(
        isinstance(args[0], int)
        for name, args, kw in world.starts
        if name == "BuilderBrainSourceBuildWorkflow"
    )
    reply = host(world, row)
    assert reply.status_code == 202, reply.content
    result = reply.json()
    assert result["project_guid"] == str(world.project.guid)
    assert result["brain_guid"] == str(world.brain.guid)
    assert result["status"] == "hosted" and result["deployment_status"] == "pending"
    assert result["data_persistent"] is False and result["runtime_verified"] is False
    row.refresh_from_db()
    app = row.hosted_app
    assert app.project_id == world.project.pk and app.default_tenant_cluster_id == world.cluster.pk
    assert app.expires_at and app.delete_data_on_expiry is False
    assert BrainAppBinding.objects.get(registered_app=app).granted_by_id == world.user.pk
    assert row.deployment.triggered_by_user_id == world.user.pk
    assert row.deployment.image_digest == DIGEST
    assert "BRAIN_TOKEN" not in app.manifest_raw
    assert 'kind = "empty_dir"' in app.manifest_raw
    expiry_before = app.expires_at
    repeated = host(world, row)
    assert repeated.status_code == 202, repeated.content
    app.refresh_from_db()
    assert app.expires_at == expiry_before
    assert BrainAppBinding.objects.filter(registered_app=app).count() == 1
    assert BuilderBrainBuild.objects.count() == 1


def test_archive_is_immutable_after_later_source_sync(world):
    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    changed = Client().put(
        f"/api/builder/v1/dev-environments/{world.dev.guid}/files/",
        data=json.dumps({"files": {"main.py": "changed later"}}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + world.bearer,
    )
    assert changed.status_code == 200, changed.content
    with source.builder_artifacts.storage().open(row.archive_key, "rb") as stream:
        raw = stream.read()
    import hashlib

    assert hashlib.sha256(raw).hexdigest() == row.archive_sha256
    source.build(row.pk)
    assert host(world, row).status_code == 202


def test_active_archive_download_is_private_and_stops_after_build(world):
    grants = []

    def during_apply(manifests):
        secret = next(item for item in manifests if item["kind"] == "Secret")
        config = json.loads(secret["stringData"]["config"])
        grants.append(config)
        response = Client().get(config["url"], HTTP_AUTHORIZATION="BuilderBuildArchive " + config["token"])
        assert response.status_code == 200
        import hashlib

        assert hashlib.sha256(b"".join(response.streaming_content)).hexdigest() == config["sha256"]

    world.native.apply_hook = during_apply
    row = ready(world)
    grant = grants[0]
    assert Client().get(grant["url"]).status_code == 403
    assert (
        Client().get(grant["url"], HTTP_AUTHORIZATION="BuilderBuildArchive " + grant["token"]).status_code
        == 403
    )
    assert grant["token"] not in json.dumps(source.response(row))


def test_current_policy_refusal_rolls_back_app_binding_and_deployment(world):
    row = ready(world)
    world.org.temporary_app_policy = {"max_ttl_seconds": 3600}
    world.org.save()
    reply = host(world, row)
    assert reply.status_code == 403, reply.content
    assert not RegisteredApp.objects.filter(slug="source-app", organization=world.org).exists()
    assert not BrainAppBinding.objects.exists()
    row.refresh_from_db()
    assert row.hosted_app_id is None and row.deployment_id is None


def test_existing_native_origin_guard_is_not_bypassed(world, monkeypatch):
    row = ready(world)
    monkeypatch.setattr(
        "astrolift_lifecycle.deployment_identity_origin.native_origin_required", lambda *a, **k: True
    )
    reply = host(world, row)
    assert reply.status_code == 403, reply.content
    assert not RegisteredApp.objects.filter(slug="source-app", organization=world.org).exists()
    assert not BrainAppBinding.objects.exists()


@pytest.mark.parametrize("destination_kind", ["other_org", "unrelated_project", "wrong_team_token"])
def test_exact_destination_and_bearer_ceiling_refuse_before_native_effects(world, destination_kind):
    from astrolift_identity.models import Organization, Team

    body = payload(world)
    if destination_kind == "other_org":
        other_org = Organization.objects.create(name="Other source org", slug="other-source-org")
        other_team = Team.objects.create(organization=other_org, name="Other", slug="other")
        project = Project.objects.create(organization=other_org, team=other_team, name="Other", slug="other")
        body["project_guid"] = str(project.guid)
    elif destination_kind == "unrelated_project":
        project = Project.objects.create(
            organization=world.org, team=world.team, name="Unrelated", slug="unrelated"
        )
        body["project_guid"] = str(project.guid)
    else:
        other_team = Team.objects.create(organization=world.org, name="Other team", slug="other-team")
        world.token.team = other_team
        world.token.save()
    result = create(world, body)
    assert result.status_code in (403, 404), result.content
    assert not BuilderBrainBuild.objects.exists() and not world.native.applied


@pytest.mark.parametrize("change", ["source", "project", "brain", "ttl", "boolean", "name"])
def test_mismatched_or_invalid_admission_has_no_native_effects(world, change):
    body = payload(world)
    if change == "source":
        body["expected_archive_sha256"] = "0" * 64
    elif change == "project":
        body["project_guid"] = str(uuid4())
    elif change == "brain":
        body["brain_slug"] = "absent"
    elif change == "ttl":
        body["expires_in"] = "1m"
    elif change == "name":
        body["app_name"] = "x" * 201
    else:
        body["delete_data_on_expiry"] = "false"
    result = create(world, body)
    assert result.status_code in (400, 404, 409), result.content
    assert not BuilderBrainBuild.objects.exists() and not world.native.applied


def test_idempotency_conflict_does_not_create_second_intent(world):
    first = create(world)
    assert first.status_code == 202, first.content
    again = create(world)
    assert again.status_code == 202 and again.json()["id"] == first.json()["id"]
    conflict = create(world, payload(world, expires_in="5h"))
    assert conflict.status_code == 409
    assert BuilderBrainBuild.objects.count() == 1 and not world.native.applied


@pytest.mark.parametrize("change", ["missing", "mutable", "wrong_org", "duplicate_repo", "unattested"])
def test_configuration_refuses_before_intent_or_native_writes(world, settings, change):
    if change == "missing":
        settings.BUILDER_BRAIN_BUILD_TARGETS = {}
    elif change == "mutable":
        world.registry.immutable = False
    elif change == "wrong_org":
        world.target["organization_guid"] = str(uuid4())
    elif change == "duplicate_repo":
        settings.BUILDER_BRAIN_BUILD_TARGETS[str(uuid4())] = dict(world.target)
    else:
        world.target["attested_project_isolation"] = False
    reply = create(world)
    assert reply.status_code == 409, reply.content
    assert not world.native.applied and not BuilderBrainBuild.objects.exists()


@pytest.mark.parametrize("change", ["token", "scope", "role", "source_owner", "project_team"])
def test_revoked_authority_refuses_before_build_and_host(world, change):
    row = ready(world)
    if change == "token":
        world.token.is_revoked = True
        world.token.save()
    elif change == "scope":
        world.token.scopes = ["read:apps"]
        world.token.save()
    elif change == "role":
        Role.objects.filter(organization=world.org).update(permissions=[])
    elif change == "source_owner":
        from django.contrib.auth import get_user_model

        world.dev.creator = get_user_model().objects.create_user(username="source-replacement-owner")
        world.dev.save()
    else:
        from astrolift_identity.models import Team

        other = Team.objects.create(organization=world.org, name="Other", slug="other")
        world.project.team = other
        world.project.save()
    before = len(world.native.applied)
    result = host(world, row)
    assert result.status_code in (401, 403, 404), result.content
    assert not BrainAppBinding.objects.exists()
    assert len(world.native.applied) == before


@pytest.mark.parametrize("failure", ["job", "digest", "cleanup"])
def test_failed_build_never_hosts_stub_and_can_retry_same_intent(world, failure):
    response = create(world)
    assert response.status_code == 202, response.content
    row = BuilderBrainBuild.objects.get()
    world.native.fail = failure == "job"
    world.native.push = failure != "digest"
    world.native.delete_failure = failure == "cleanup"
    with pytest.raises(RuntimeError):
        source.build(row.pk)
    row.refresh_from_db()
    assert row.status == "failed" and not row.hosted_app_id
    assert host(world, row).status_code == 409
    world.native.fail, world.native.push, world.native.delete_failure = False, True, False
    source.build(row.pk)
    row.refresh_from_db()
    assert row.status == "artifact_ready" and not world.native.objects
    assert BuilderBrainBuild.objects.count() == 1


def test_cleanup_refuses_replacement_job_uid(world):
    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    world.native.delete_failure = True
    with pytest.raises(RuntimeError):
        source.build(row.pk)
    row.refresh_from_db()
    key = next(key for key in world.native.objects if key[0] == "Job")
    world.native.objects[key]["metadata"]["uid"] = "replacement"
    world.native.delete_failure = False
    with pytest.raises(RuntimeError, match="CLEANUP_RESOURCE_CHANGED"):
        source.build(row.pk)
    assert world.native.objects[key]["metadata"]["uid"] == "replacement"


def test_preexisting_registry_tag_without_owned_successful_build_is_not_adopted(world):
    from _sdk.registry import Tag

    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    world.registry.tags = [Tag(source._tag(row), DIGEST, None)]
    with pytest.raises(RuntimeError, match="IMAGE_PROVENANCE_UNCONFIRMED"):
        source.build(row.pk)
    row.refresh_from_db()
    assert row.status == "failed" and not row.image_ref and not world.native.applied


def test_revoked_source_authority_prevents_any_build_native_write(world):
    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    world.token.is_revoked = True
    world.token.save()
    with pytest.raises(RuntimeError, match="ORIGINAL_CREDENTIAL_REVOKED"):
        source.build(row.pk)
    assert not world.native.applied


def test_source_app_uses_normal_brain_secrets_and_expiry_teardown(world, monkeypatch):
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

    from astrolift_registry import brain_apps, expiry
    from astrolift_registry.expiry_cleanup import retire_runtime
    from astrolift_registry.tests.test_expiry_cleanup_2433 import NativeObjects
    from astrolift_workflows.activities.app_teardown import _soft_delete_app_records_sync
    from core.app_deploy import render_resources_for_deployment

    row = ready(world)
    result = host(world, row)
    assert result.status_code == 202, result.content
    row.refresh_from_db()
    app, deployment = row.hosted_app, row.deployment
    namespace = app.k8s_namespace
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
    resources = render_resources_for_deployment(deployment, include_managed_filesystems=False)
    for item in resources:
        objects.put(item)
    pod = next(item for item in resources if item["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"] == row.image_ref
    assert {v["name"] for v in pod["volumes"] if "emptyDir" in v} == {"scratch", "data"}
    secret = brain_apps.brain_secret(deployment, namespace)
    assert secret and secret["data"]["BRAIN_TOKEN"]
    objects.put(secret)
    secret_name = secret["metadata"]["name"]
    driver = K8sNativeClusterDriver(config=K8sNativeConfig(), k8s_client_factory=lambda **kwargs: objects)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: driver)
    assert expiry.fire(app.pk, now=app.expires_at)
    retire_runtime(app.pk)
    assert objects.get(kind="Namespace", namespace=None, name=namespace)
    assert not objects.get(kind="Secret", namespace=namespace, name=secret_name)
    summary = _soft_delete_app_records_sync(app.pk)
    assert summary["brain_bindings"] == 1
    assert not BrainAppBinding.objects.filter(registered_app=app).exists()
    assert host(world, row).status_code == 409
    world.brain.refresh_from_db()
    assert world.brain.deleted_at is None and world.brain.expires_at is None


@pytest.mark.parametrize("change", ["uid", "label", "incomplete"])
def test_completion_by_name_cannot_prove_the_original_owned_job(world, change):
    assert create(world).status_code == 202
    row = BuilderBrainBuild.objects.get()

    def replace_completion(job):
        if change == "uid":
            job["metadata"]["uid"] = "replacement-successful-job"
        elif change == "label":
            job["metadata"]["labels"][source.LABEL] = str(uuid4())
        else:
            job["status"] = {"succeeded": 0}

    world.native.completion_hook = replace_completion
    with pytest.raises(RuntimeError, match="IMAGE_PROVENANCE_UNCONFIRMED|CLEANUP_RESOURCE_CHANGED"):
        source.build(row.pk)
    row.refresh_from_db()
    assert row.status == "failed" and not row.image_digest and not row.image_ref
    assert all("completed_image_digest" not in ref for ref in row.cleanup_receipt.values())
    if change != "incomplete":
        assert any(key[0] == "Job" for key in world.native.objects)


@pytest.mark.parametrize("change", ["role", "group", "policy"])
def test_background_revalidation_discards_inherited_authority_cache(world, change):
    from astrolift_identity import abac
    from astrolift_identity.models import Member, Policy, RoleBinding

    assert create(world).status_code == 202
    row = BuilderBrainBuild.objects.get()
    members = Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    if change == "group":
        RoleBinding.objects.filter(user=world.user).update(user=None, group_external_id="source-builders")
        members.update(idp_groups=["source-builders"])
    attrs = abac.RequestAttributes(actor_user_id=world.user.pk)
    with abac.request_attributes(attrs):
        source.validate(row)
        # Deliberately warm the enclosing long-lived context's caches too.
        from astrolift_identity.permission_resolver import actor_groups
        from core.tenancy import TenantContext

        actor_groups(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk))
        abac.org_policies(world.org.pk, attrs)
        if change == "role":
            Role.objects.filter(organization=world.org).update(permissions=[])
        elif change == "group":
            members.update(idp_groups=[])
        else:
            Policy.objects.create(
                organization=world.org,
                name="New denial",
                slug="new-build-denial",
                scope_level="ORG",
                action_pattern="app.deploy",
                effect="DENY",
                conditions=[],
            )
        with pytest.raises(RuntimeError):
            source.build(row.pk)
    assert not world.native.applied
