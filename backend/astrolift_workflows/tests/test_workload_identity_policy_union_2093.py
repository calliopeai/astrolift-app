"""Real app/environment rows reconcile through the actual IRSA IAM driver boundary."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import boto3
import pytest
from aws.identity_irsa import IRSAConfig, IRSADriver
from django.db import close_old_connections, connection
from moto import mock_aws

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_workflows.activities.workload_identity import _ensure_workload_identity_sync
from core.app_deploy import workload_identity_role_name

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(app, env, cluster, monkeypatch):
    aws, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "AWS", "capabilities_manifest": {}, "config_schema": {}}
    )
    cluster.provider_plugin = aws
    cluster.auth_config = {"cluster_oidc_issuer": "oidc.eks.us-east-1.amazonaws.com/id/ABC"}
    cluster.save()
    preview = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="preview-pr-3", k8s_namespace="preview-pr-3"
    )
    production = service(app, env, "production-data")
    branch = service(app, preview, "preview-data")
    calls = []

    def binding(row):
        calls.append(row.pk)
        return SimpleNamespace(
            iam_grants=[SimpleNamespace(resource=f"arn:aws:s3:::{row.name}/*", actions=["s3:GetObject"])]
        )

    monkeypatch.setattr(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for", binding
    )
    with mock_aws():
        client = boto3.client(
            "iam", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
        )
        driver = IRSADriver(
            config=IRSAConfig(
                region="us-east-1",
                account_id="123456789012",
                cluster_oidc_issuer=cluster.auth_config["cluster_oidc_issuer"],
            ),
            iam_client=client,
        )
        monkeypatch.setattr(
            "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver",
            lambda *args: driver,
        )
        yield SimpleNamespace(
            app=app,
            prod=env,
            preview=preview,
            cluster=cluster,
            production=production,
            branch=branch,
            client=client,
            driver=driver,
            calls=calls,
        )


def service(app, env, name):
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        name=name,
        kind=ManagedService.Kind.OBJECT_STORE,
        backend_ref=name,
    )


def resources(world):
    return {
        s["Resource"]
        for s in world.client.get_role_policy(
            RoleName=workload_identity_role_name(world.app), PolicyName="astrolift-workload-policy"
        )["PolicyDocument"]["Statement"]
    }


def expected(world):
    return {f"arn:aws:s3:::{s.name}/*" for s in [world.production, world.branch]}


@pytest.mark.parametrize("preview_first", [False, True])
def test_each_environment_reconciles_the_complete_policy_and_namespace_trust(world, preview_first):
    envs = [world.prod, world.preview]
    if preview_first:
        envs.reverse()
    for env in envs + envs:
        result = _ensure_workload_identity_sync(world.app.pk, env.pk)
        assert result["grants"] == 2
        assert resources(world) == expected(world)
        role = world.client.get_role(RoleName=result["role"])["Role"]
        subjects = role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["StringEquals"][
            "oidc.eks.us-east-1.amazonaws.com/id/ABC:sub"
        ]
        assert set(subjects) == {
            f"system:serviceaccount:acme-test-hello-app:{result['role']}",
            f"system:serviceaccount:preview-pr-3:{result['role']}",
        }


@pytest.mark.parametrize(
    "invalid",
    ["deleted-env", "deleted-service", "wrong-cluster", "foreign-app", "wrong-owner", "deleted-attachment"],
)
def test_incoherent_or_retired_consumers_never_reach_the_driver(world, invalid):
    foreign_org = Organization.objects.create(name="Foreign", slug="foreign-2093")
    foreign_team = Team.objects.create(organization=foreign_org, name="foreign", slug="foreign")
    foreign_project = Project.objects.create(
        organization=foreign_org, team=foreign_team, name="foreign", slug="foreign"
    )
    foreign_app = RegisteredApp.objects.create(
        organization=foreign_org, team=foreign_team, project=foreign_project, name="foreign", slug="foreign"
    )
    other_cluster = TenantCluster.objects.create(
        organization=foreign_org,
        provider_plugin=world.cluster.provider_plugin,
        name="other",
        slug="other",
        endpoint="https://other.invalid",
    )
    extra = AppEnvironment.objects.create(
        registered_app=world.app, tenant_cluster=world.cluster, name="extra", k8s_namespace="extra"
    )
    bad = service(world.app, extra, "must-not-grant")
    if invalid == "deleted-env":
        extra.soft_delete()
    elif invalid == "deleted-service":
        bad.soft_delete()
    elif invalid == "wrong-cluster":
        extra.tenant_cluster = other_cluster
        extra.save()
    elif invalid == "foreign-app":
        bad.registered_app = foreign_app
        bad.save()
    elif invalid == "wrong-owner":
        extra.registered_app = foreign_app
        extra.save()
    else:
        # An otherwise valid app-private source environment on this cluster is not
        # a consumer of this app's role when only its deleted attachment links it.
        bad.registered_app = None
        bad.app_environment = None
        bad.project = world.app.project
        bad.tenant_cluster = world.cluster
        bad.save()
        attachment = ManagedServiceAttachment.objects.create(managed_service=bad, app_environment=extra)
        attachment.soft_delete()
    _ensure_workload_identity_sync(world.app.pk, world.preview.pk)
    assert resources(world) == expected(world)
    assert bad.pk not in world.calls


@pytest.mark.parametrize("target", ["foreign-cluster", "deleted-cluster", "deleted-app", "deleted-org"])
def test_invalid_reconcile_target_fails_before_iam(world, target):
    if target == "foreign-cluster":
        foreign = Organization.objects.create(name="other", slug="other-target")
        world.cluster.organization = foreign
        world.cluster.save()
    elif target == "deleted-cluster":
        world.cluster.soft_delete()
    elif target == "deleted-app":
        world.app.soft_delete()
    else:
        world.app.organization.soft_delete()
    with pytest.raises((ValueError, RegisteredApp.DoesNotExist)):
        _ensure_workload_identity_sync(world.app.pk, world.preview.pk)
    assert not world.calls
    assert world.client.list_roles()["Roles"] == []


def test_shared_project_binding_is_deduplicated_and_wrong_project_or_cluster_is_excluded(world):
    shared = ManagedService.objects.create(
        project=world.app.project,
        tenant_cluster=world.cluster,
        name="shared",
        kind=ManagedService.Kind.OBJECT_STORE,
        backend_ref="shared",
    )
    ManagedServiceAttachment.objects.create(managed_service=shared, app_environment=world.prod)
    ManagedServiceAttachment.objects.create(managed_service=shared, app_environment=world.preview)
    _ensure_workload_identity_sync(world.app.pk, world.preview.pk)
    assert resources(world) == expected(world) | {"arn:aws:s3:::shared/*"}
    assert world.calls.count(shared.pk) == 1
    shared.tenant_cluster = TenantCluster.objects.create(
        organization=world.app.organization,
        provider_plugin=world.cluster.provider_plugin,
        name="other",
        slug="other-local",
        endpoint="https://other.invalid",
    )
    shared.save()
    _ensure_workload_identity_sync(world.app.pk, world.prod.pk)
    assert resources(world) == expected(world)
    shared.tenant_cluster = world.cluster
    shared.project = Project.objects.create(
        organization=world.app.organization, team=world.app.team, name="other", slug="other-local"
    )
    shared.save()
    _ensure_workload_identity_sync(world.app.pk, world.prod.pk)
    assert resources(world) == expected(world)


def test_retiring_one_environment_removes_its_grants_without_stripping_the_other(world):
    _ensure_workload_identity_sync(world.app.pk, world.preview.pk)
    world.preview.soft_delete()
    _ensure_workload_identity_sync(world.app.pk, world.prod.pk)
    assert resources(world) == {"arn:aws:s3:::production-data/*"}


def test_concurrent_environment_deploys_leave_one_complete_policy_and_trust(world):
    barrier = Barrier(2)

    def run(env_id):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return _ensure_workload_identity_sync(world.app.pk, env_id)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [world.prod.pk, world.preview.pk]))
    assert all(row["grants"] == 2 for row in results)
    assert resources(world) == expected(world)
    subjects = world.client.get_role(RoleName=results[0]["role"])["Role"]["AssumeRolePolicyDocument"][
        "Statement"
    ][0]["Condition"]["StringEquals"]["oidc.eks.us-east-1.amazonaws.com/id/ABC:sub"]
    assert len(set(subjects)) == 2


def test_an_empty_union_removes_retired_iam_grants(world, monkeypatch):
    _ensure_workload_identity_sync(world.app.pk, world.preview.pk)
    monkeypatch.setattr(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        lambda row: SimpleNamespace(iam_grants=[]),
    )
    result = _ensure_workload_identity_sync(world.app.pk, world.prod.pk)
    assert result["grants"] == 0
    assert world.client.list_role_policies(RoleName=result["role"])["PolicyNames"] == []
