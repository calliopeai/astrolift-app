"""Podless AWS deployment images are protected before any service or Job write."""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import boto3
import pytest
from aws._errors import ProviderError
from aws.managed.faas_lambda import LambdaConfig, LambdaDriver
from aws.registry_ecr import ECRConfig, ECRDriver
from botocore.stub import Stubber
from moto import mock_aws

from astrolift_lifecycle.models import Deployment
from astrolift_services.models import ManagedService
from astrolift_workflows.activities import faas, static_site
from astrolift_workflows.activities.managed_service_lifecycle import build_provision_spec
from providers.tests.aws.test_managed_faas_lambda import FakeIAM, FakeLambda

pytestmark = pytest.mark.django_db

FAAS = """
name = "hello"
[[workloads]]
name = "api"
kind = "faas"
"""


class ClusterFake:
    def __init__(self):
        self.applied = []

    def apply_manifests(self, cluster, namespace, resources):
        self.applied.append(resources)
        return SimpleNamespace(ok=True)

    def get_workload_status(self, *args):
        return SimpleNamespace(conditions=[{"type": "Complete", "status": "True"}])


class IdentityFake:
    def __init__(self):
        self.calls = []

    def create_identity_role(self, name, policy):
        self.calls.append(("role", name, policy))

    def bind_service_account(self, *args):
        self.calls.append(("bind", args))
        return {"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/static-build"}


@pytest.fixture
def world(app, env, cluster, monkeypatch):
    cluster.provider_plugin.slug = "aws"
    cluster.provider_plugin.save(update_fields=["slug"])
    cluster.region = "us-east-1"
    cluster.provider_config = {"region": "us-east-1", "account_id": "123456789012"}
    cluster.save(update_fields=["region", "provider_config", "updated_at", "version"])
    lambda_client, iam_client = FakeLambda(), FakeIAM()
    lambda_driver = LambdaDriver(
        config=LambdaConfig(region="us-east-1"), client=lambda_client, iam_client=iam_client
    )
    cluster_driver, identity_driver = ClusterFake(), IdentityFake()
    with mock_aws():
        ecr_client = boto3.client(
            "ecr", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
        )
        registry = ECRDriver(
            config=ECRConfig(region="us-east-1", account_id="123456789012"), client=ecr_client
        )
        repo = registry.ensure_repo("acme/hello")
        manifest = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                "config": {
                    "mediaType": "application/vnd.docker.container.image.v1+json",
                    "size": 1,
                    "digest": "sha256:" + "a" * 64,
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                        "size": 1,
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            }
        )
        image = ecr_client.put_image(repositoryName=repo.name, imageManifest=manifest, imageTag="build-one")
        digest = image["image"]["imageId"]["imageDigest"]
        builder = ecr_client.put_image(
            repositoryName=repo.name, imageManifest=manifest.replace("a" * 64, "b" * 64), imageTag="builder"
        )
        builder_digest = builder["image"]["imageId"]["imageDigest"]
        app.manifest_raw = FAAS
        app.registry_repo_uri = repo.uri
        app.save(update_fields=["manifest_raw", "registry_repo_uri", "updated_at", "version"])
        monkeypatch.setattr(
            "core.app_deploy.driver_for_capability",
            lambda cluster, capability: {"registry": registry, "identity": identity_driver}[capability],
        )
        monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: cluster_driver)
        monkeypatch.setattr(
            "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug=cluster.slug)
        )
        build_image_module = importlib.import_module("astrolift_workflows.activities.build_image")
        monkeypatch.setattr(build_image_module, "_ensure_cluster_oidc_issuer", lambda cluster: None)
        monkeypatch.setattr(
            build_image_module,
            "_resolve_source_url",
            lambda app, commit: "git+https://example.test/app#main",
        )

        def provision(row):
            result = lambda_driver.provision(build_provision_spec(row, cluster=cluster))
            assert result.ok, result.message
            if "create_function" in lambda_client.names():
                created = lambda_client.kwargs_for("create_function")
                lambda_client._function_tags[created["FunctionName"]] = created["Tags"]
            row.backend_ref = result.handle
            row.status = ManagedService.Status.ACTIVE
            row.save(update_fields=["backend_ref", "status", "updated_at", "version"])

        monkeypatch.setattr(faas, "_provision_row", provision)
        yield SimpleNamespace(
            app=app,
            env=env,
            cluster=cluster,
            registry=registry,
            ecr=ecr_client,
            repo=repo,
            digest=digest,
            builder_digest=builder_digest,
            lambda_client=lambda_client,
            iam_client=iam_client,
            cluster_driver=cluster_driver,
            identity=identity_driver,
        )


def _deployment(world, *, image_tag="build-one", digest="", snapshot=None):
    return Deployment.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        trigger_kind="manual",
        image_tag=image_tag,
        image_digest=digest,
        config_snapshot=snapshot or {},
    )


def _static_build(world, deployment):
    return static_site._platform_build_workload(
        deployment=deployment,
        app=world.app,
        cluster=world.cluster,
        workload=SimpleNamespace(name="site", static_build_command="npm run build", static_output_dir="dist"),
        bucket="app-assets",
        distribution_id="EXAMPLE",
        region="us-east-1",
        account_id="123456789012",
        commit_sha="abc",
    )


@pytest.mark.parametrize("digest_known", [False, True])
def test_faas_provision_uses_protected_digest_and_retry_preserves_pin(world, digest_known):
    deployment = _deployment(world, digest=world.digest if digest_known else "")
    faas._ensure_faas_services_sync(deployment.pk)
    deployment.refresh_from_db()
    original_pins = deployment.config_snapshot["ecr_retention_pins"]
    assert len(original_pins) == 1
    assert deployment.guid.hex in original_pins[0]["tag"]
    expected = f"{world.repo.uri}@{world.digest}"
    assert world.lambda_client.kwargs_for("create_function")["Code"]["ImageUri"] == expected
    assert ManagedService.objects.get(kind="faas").config["image_uri"] == expected
    faas._ensure_faas_services_sync(deployment.pk)
    assert world.lambda_client.kwargs_for("update_function_code")["ImageUri"] == expected
    deployment.refresh_from_db()
    assert deployment.config_snapshot["ecr_retention_pins"] == original_pins


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("already_provisioned", [False, True])
def test_missing_faas_image_fails_before_service_create_or_update(world, already_provisioned, public):
    if already_provisioned:
        faas._ensure_faas_services_sync(_deployment(world).pk)
    if public:
        world.app.manifest_raw = FAAS + "faas_public = true\n"
        world.app.save(update_fields=["manifest_raw", "updated_at", "version"])
    before_services = list(ManagedService.all_objects.order_by("pk").values())
    before_lambda = list(world.lambda_client.calls)
    before_iam = list(world.iam_client.calls)
    deployment = _deployment(world, image_tag="missing-image")
    with pytest.raises(ProviderError, match="unavailable"):
        faas._ensure_faas_services_sync(deployment.pk)
    assert list(ManagedService.all_objects.order_by("pk").values()) == before_services
    assert world.lambda_client.calls == before_lambda
    assert world.iam_client.calls == before_iam


def test_faas_pin_write_denied_blocks_service_and_snapshot_writes(world):
    deployment = _deployment(world)
    response = world.ecr.batch_get_image(repositoryName=world.repo.name, imageIds=[{"imageTag": "build-one"}])
    with Stubber(world.ecr) as stubber:
        stubber.add_response("batch_get_image", response)
        stubber.add_client_error("put_image", service_error_code="AccessDeniedException")
        with pytest.raises(ProviderError):
            faas._ensure_faas_services_sync(deployment.pk)
        stubber.assert_no_pending_responses()
    deployment.refresh_from_db()
    assert not deployment.config_snapshot.get("ecr_retention_pins")
    assert not ManagedService.objects.exists()
    assert world.lambda_client.calls == []
    assert world.iam_client.calls == []


def test_faas_retry_keeps_pinned_digest_when_source_tag_moves(world):
    deployment = _deployment(world)
    faas._ensure_faas_services_sync(deployment.pk)
    builder = world.ecr.batch_get_image(repositoryName=world.repo.name, imageIds=[{"imageTag": "builder"}])[
        "images"
    ][0]
    world.ecr.batch_delete_image(repositoryName=world.repo.name, imageIds=[{"imageTag": "build-one"}])
    world.ecr.put_image(
        repositoryName=world.repo.name, imageManifest=builder["imageManifest"], imageTag="build-one"
    )
    with Stubber(world.ecr):
        faas._ensure_faas_services_sync(deployment.pk)
        deployment.image_digest = world.digest
        deployment.save(update_fields=["image_digest", "updated_at", "version"])
        # The canonical digest produced by an earlier stage is cached too.
        faas._ensure_faas_services_sync(deployment.pk)
    updates = [
        kwargs["ImageUri"] for name, kwargs in world.lambda_client.calls if name == "update_function_code"
    ]
    assert updates == [f"{world.repo.uri}@{world.digest}"] * 2


def test_absent_faas_image_reference_fails_before_service_writes(world):
    world.app.registry_repo_uri = ""
    world.app.save(update_fields=["registry_repo_uri", "updated_at", "version"])
    with pytest.raises(RuntimeError, match="no deployment image reference"):
        faas._ensure_faas_services_sync(_deployment(world).pk)
    assert not ManagedService.objects.exists()
    assert world.lambda_client.calls == []
    assert world.iam_client.calls == []


def test_rollback_faas_deployment_creates_its_own_pin(world):
    source = _deployment(world)
    faas._ensure_faas_services_sync(source.pk)
    source.refresh_from_db()
    original = source.config_snapshot["ecr_retention_pins"]
    rollback = _deployment(world, digest=world.digest, snapshot=source.config_snapshot)
    faas._ensure_faas_services_sync(rollback.pk)
    rollback.refresh_from_db()
    assert len(rollback.config_snapshot["ecr_retention_pins"]) == 1
    assert rollback.guid.hex in rollback.config_snapshot["ecr_retention_pins"][0]["tag"]
    source.refresh_from_db()
    assert source.config_snapshot["ecr_retention_pins"] == original


def test_static_builder_private_mirror_is_pinned_before_job_and_preserves_faas_pin(world, monkeypatch):
    builder = f"{world.repo.uri}:builder"
    monkeypatch.setattr("providers.k8s_native.build_static.STATIC_BUILDER_IMAGE", builder)
    deployment = _deployment(world)
    faas._ensure_faas_services_sync(deployment.pk)
    assert _static_build(world, deployment)["synced"] is True
    jobs = [row for group in world.cluster_driver.applied for row in group if row["kind"] == "Job"]
    assert (
        jobs[0]["spec"]["template"]["spec"]["containers"][0]["image"]
        == f"{world.repo.uri}@{world.builder_digest}"
    )
    deployment.refresh_from_db()
    pins = deployment.config_snapshot["ecr_retention_pins"]
    assert {pin["source_ref"] for pin in pins} == {f"{world.repo.uri}:build-one", builder}
    assert all(deployment.guid.hex in pin["tag"] for pin in pins)


def test_missing_static_builder_fails_before_identity_or_job_writes(world, monkeypatch):
    monkeypatch.setattr("providers.k8s_native.build_static.STATIC_BUILDER_IMAGE", f"{world.repo.uri}:missing")
    with pytest.raises(ProviderError, match="unavailable"):
        _static_build(world, _deployment(world))
    assert world.identity.calls == []
    assert world.cluster_driver.applied == []


def test_external_static_builder_remains_unchanged(world):
    from providers.k8s_native.build_static import STATIC_BUILDER_IMAGE

    deployment = _deployment(world)
    assert _static_build(world, deployment)["synced"] is True
    job = world.cluster_driver.applied[0][1]
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == STATIC_BUILDER_IMAGE
    deployment.refresh_from_db()
    assert not deployment.config_snapshot.get("ecr_retention_pins")
