"""A re-pointed cluster's per-app roles trust its new OIDC issuer (#2472).

Real rows on Postgres, the real IRSA driver resolved for the cluster
(``driver_for_capability``) and IAM on moto. The last test runs the whole
seam: ``register_tenant_cluster`` re-points the row the way the installer's
region change does, and the workflow it starts on the Temporal test server
moves the roles.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import unquote

import boto3
import pytest
from asgiref.sync import sync_to_async
from aws.identity_irsa import IRSAConfig, IRSADriver
from django.core.management import call_command
from moto import mock_aws
from temporalio.exceptions import ApplicationError

from astrolift_clusters.management.commands import register_tenant_cluster as cmd
from astrolift_clusters.models import ClusterWorkloadIdentityRebind, ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.workload_identity_rebind import (
    REBIND_INCOMPLETE,
    _rebind_cluster_sync,
    rebind_cluster_workload_identity,
)
from astrolift_workflows.workflows.rebind_cluster_workload_identity import (
    RebindClusterWorkloadIdentityWorkflow,
)
from core.app_deploy import workload_identity_role_name
from core.testing.temporal import temporal_worker

pytestmark = pytest.mark.django_db

ACCOUNT = "123456789012"
OLD = "oidc.eks.us-east-1.amazonaws.com/id/OLD"
MID = "oidc.eks.eu-west-1.amazonaws.com/id/MID"
NEW = "oidc.eks.us-west-2.amazonaws.com/id/NEW"
GRANT = [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::data/*"}]


def _app(org, team, project, slug, **kwargs):
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=slug,
        slug=slug,
        provisioning_status="ready",
        **kwargs,
    )


def _environment(app, cluster, name="production"):
    return AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name=name, k8s_namespace=f"{app.slug}-{name}"
    )


def _build_world(org, team, project, app, env, cluster):
    """The restored database just after the target registered under the old slug."""
    aws, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "AWS", "capabilities_manifest": {}, "config_schema": {}}
    )
    TenantCluster.all_objects.filter(pk=cluster.pk).update(
        provider_plugin=aws,
        region="us-west-2",
        auth_config={"cluster_name": "target", "region": "us-west-2", "cluster_oidc_issuer": NEW},
        provider_config={"account_id": ACCOUNT, "region": "us-west-2", "cluster_oidc_issuer": NEW},
    )
    cluster.refresh_from_db()
    second = _app(org, team, project, "second-app")
    _environment(second, cluster)
    _environment(second, cluster, "staging")
    by_default = _app(org, team, project, "default-app", default_tenant_cluster=cluster)
    elsewhere_cluster = TenantCluster.objects.create(
        organization=org,
        slug="elsewhere-cluster",
        name="elsewhere",
        provider_plugin=aws,
        auth_method="exec_plugin",
        auth_config={"cluster_oidc_issuer": OLD},
    )
    elsewhere = _app(org, team, project, "elsewhere-app")
    _environment(elsewhere, elsewhere_cluster)
    gone = _app(org, team, project, "gone-app")
    _environment(gone, cluster)
    gone.soft_delete()
    retired = _app(org, team, project, "retired-app")
    _environment(retired, cluster).soft_delete()
    foreign_org = Organization.objects.create(name="Foreign", slug="foreign-2472")
    foreign_team = Team.objects.create(organization=foreign_org, name="t", slug="t-2472")
    foreign_project = Project.objects.create(
        organization=foreign_org, team=foreign_team, name="p", slug="p-2472"
    )
    foreign = _app(foreign_org, foreign_team, foreign_project, "foreign-app")
    _environment(foreign, cluster)

    old = IRSADriver(
        config=IRSAConfig(region="us-east-1", account_id=ACCOUNT, cluster_oidc_issuer=OLD),
        iam_client=boto3.client("iam", region_name="us-east-1"),
    )
    roles = {}
    for each in (app, second, by_default, elsewhere, gone, retired, foreign):
        role = workload_identity_role_name(each)
        old.create_identity_role(role, GRANT)
        old.bind_service_account("old", f"{each.slug}-production", role, role)
        roles[each.slug] = role
    return SimpleNamespace(
        cluster=cluster,
        app=app,
        second=second,
        roles=roles,
        moved={app.slug, second.slug, by_default.slug},
        untouched={elsewhere.slug, gone.slug, retired.slug, foreign.slug},
    )


@pytest.fixture
def world(org, team, project, app, env, cluster):
    with mock_aws():
        world = _build_world(org, team, project, app, env, cluster)
        world.rebind = ClusterWorkloadIdentityRebind.objects.create(
            tenant_cluster=cluster, issuer=NEW, previous_issuers=[OLD]
        )
        yield world


def _statement(role: str) -> dict:
    iam = boto3.client("iam", region_name="us-east-1")
    document = iam.get_role(RoleName=role)["Role"]["AssumeRolePolicyDocument"]
    [statement] = (json.loads(unquote(document)) if isinstance(document, str) else document)["Statement"]
    return statement


def _trusts(role: str, issuer: str, namespace: str) -> bool:
    statement = _statement(role)
    return statement["Principal"][
        "Federated"
    ] == f"arn:aws:iam::{ACCOUNT}:oidc-provider/{issuer}" and statement["Condition"]["StringEquals"] == {
        f"{issuer}:aud": "sts.amazonaws.com",
        f"{issuer}:sub": f"system:serviceaccount:{namespace}:{role}",
    }


def test_every_app_on_the_cluster_trusts_the_new_issuer_and_nothing_else_moves(world):
    summary = _rebind_cluster_sync(world.rebind.pk)

    for slug in world.moved:
        assert _trusts(world.roles[slug], NEW, f"{slug}-production"), slug
    # Another cluster's app, a deleted app, an app whose environment here was
    # deleted and another organization's app on this org's cluster keep what
    # they had.
    for slug in world.untouched:
        assert _trusts(world.roles[slug], OLD, f"{slug}-production"), slug
    world.rebind.refresh_from_db()
    assert world.rebind.state == ClusterWorkloadIdentityRebind.State.SUCCEEDED
    assert (world.rebind.apps_total, world.rebind.apps_reconciled, world.rebind.failures) == (3, 3, [])
    assert world.rebind.started_at is not None and world.rebind.finished_at is not None
    assert summary["outcomes"] == {"rebound": 3}


def test_a_shared_cluster_moves_every_live_organizations_apps(world):
    """A shared cluster belongs to every organization, so its walk is not held
    to one; an organization since deleted is still left out."""
    TenantCluster.all_objects.filter(pk=world.cluster.pk).update(organization=None)
    defunct = Organization.objects.create(name="Defunct", slug="defunct-2472")
    defunct_team = Team.objects.create(organization=defunct, name="d", slug="d-2472")
    defunct_project = Project.objects.create(organization=defunct, team=defunct_team, name="d", slug="d-2472")
    orphan = _app(defunct, defunct_team, defunct_project, "orphan-app")
    _environment(orphan, world.cluster)
    role = workload_identity_role_name(orphan)
    old = IRSADriver(
        config=IRSAConfig(region="us-east-1", account_id=ACCOUNT, cluster_oidc_issuer=OLD),
        iam_client=boto3.client("iam", region_name="us-east-1"),
    )
    old.create_identity_role(role, GRANT)
    old.bind_service_account("old", "orphan-app-production", role, role)
    defunct.soft_delete()

    _rebind_cluster_sync(world.rebind.pk)

    assert _trusts(world.roles["foreign-app"], NEW, "foreign-app-production")
    assert _trusts(role, OLD, "orphan-app-production")
    world.rebind.refresh_from_db()
    assert (world.rebind.apps_total, world.rebind.state) == (4, ClusterWorkloadIdentityRebind.State.SUCCEEDED)


def test_a_role_that_cannot_move_fails_the_request_and_a_rerun_finishes_it(world):
    iam = boto3.client("iam", region_name="us-east-1")
    role = world.roles[world.second.slug]
    iam.untag_role(RoleName=role, TagKeys=["astrolift.io/managed-by"])

    with pytest.raises(ApplicationError) as raised:
        _rebind_cluster_sync(world.rebind.pk)

    assert raised.value.type == REBIND_INCOMPLETE and raised.value.non_retryable
    world.rebind.refresh_from_db()
    assert world.rebind.state == ClusterWorkloadIdentityRebind.State.FAILED
    assert (world.rebind.apps_total, world.rebind.apps_reconciled) == (3, 2)
    [failure] = world.rebind.failures
    assert (failure["app"], failure["role"]) == (f"acme-test/{world.second.slug}", role)
    assert "not managed by the platform" in failure["error"]
    assert _trusts(role, OLD, f"{world.second.slug}-production")

    iam.tag_role(RoleName=role, Tags=[{"Key": "astrolift.io/managed-by", "Value": "platform"}])
    summary = _rebind_cluster_sync(world.rebind.pk)

    world.rebind.refresh_from_db()
    assert world.rebind.state == ClusterWorkloadIdentityRebind.State.SUCCEEDED
    assert (world.rebind.apps_reconciled, world.rebind.failures) == (3, [])
    assert summary["outcomes"] == {"current": 2, "rebound": 1}


def test_a_superseded_request_moves_nothing(world):
    ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=world.cluster, issuer=NEW, previous_issuers=[OLD]
    )

    assert _rebind_cluster_sync(world.rebind.pk)["superseded"] is True

    for slug in world.moved:
        assert _trusts(world.roles[slug], OLD, f"{slug}-production"), slug
    world.rebind.refresh_from_db()
    assert world.rebind.state == ClusterWorkloadIdentityRebind.State.PENDING


def test_a_request_superseded_mid_walk_stops_at_the_next_app(world, monkeypatch):
    moved = []
    real = IRSADriver.rebind_trust_issuer

    def rebind_then_re_point(self, role, previous_issuers, owner):
        moved.append(role)
        ClusterWorkloadIdentityRebind.objects.create(
            tenant_cluster=world.cluster, issuer=NEW, previous_issuers=[OLD, MID]
        )
        return real(self, role, previous_issuers=previous_issuers, owner=owner)

    monkeypatch.setattr(IRSADriver, "rebind_trust_issuer", rebind_then_re_point)

    assert _rebind_cluster_sync(world.rebind.pk)["superseded"] is True
    assert len(moved) == 1


def test_an_issuer_the_cluster_no_longer_has_fails_before_any_role(world):
    TenantCluster.all_objects.filter(pk=world.cluster.pk).update(
        auth_config={"cluster_name": "target", "region": "us-west-2", "cluster_oidc_issuer": MID}
    )

    with pytest.raises(ApplicationError, match=f"OIDC issuer is {MID}, not {NEW}"):
        _rebind_cluster_sync(world.rebind.pk)

    world.rebind.refresh_from_db()
    assert world.rebind.state == ClusterWorkloadIdentityRebind.State.FAILED
    assert MID in world.rebind.error
    for slug in world.moved:
        assert _trusts(world.roles[slug], OLD, f"{slug}-production"), slug


def test_an_unregistered_cluster_fails_before_any_role(world):
    world.cluster.soft_delete()

    with pytest.raises(ApplicationError, match="unregistered or deactivated"):
        _rebind_cluster_sync(world.rebind.pk)

    for slug in world.moved:
        assert _trusts(world.roles[slug], OLD, f"{slug}-production"), slug


@pytest.mark.django_db(transaction=True)
async def test_the_registration_moves_the_roles_through_the_real_workflow(
    temporal_env, monkeypatch, settings, org, team, project, app, env, cluster
):
    """The seam the issue is about, end to end: the installer's re-point, the
    workflow it starts and the roles it moves."""

    async def client():
        return temporal_env.client

    settings.TEMPORAL_TASK_QUEUE = "astrolift-test"
    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setattr(
        cmd,
        "_discover_aws",
        lambda name, region: {
            "endpoint": "https://target.eks.invalid",
            "ca_cert": "Y2E=",
            "account_id": ACCOUNT,
            "oidc_issuer_url": f"https://{NEW}",
            "oidc_provider_arn": f"arn:aws:iam::{ACCOUNT}:oidc-provider/{NEW}",
        },
    )
    with mock_aws():
        world = await sync_to_async(_build_world)(org, team, project, app, env, cluster)
        # As the old region left it: the row still names the old cluster.
        await sync_to_async(TenantCluster.all_objects.filter(pk=cluster.pk).update)(
            region="us-east-1",
            auth_config={"cluster_name": cluster.slug, "region": "us-east-1", "cluster_oidc_issuer": OLD},
            provider_config={"account_id": ACCOUNT, "region": "us-east-1", "cluster_oidc_issuer": OLD},
        )
        async with temporal_worker(
            temporal_env,
            workflows=[RebindClusterWorkloadIdentityWorkflow],
            activities=[rebind_cluster_workload_identity],
        ):
            await sync_to_async(call_command)(
                "register_tenant_cluster",
                slug=cluster.slug,
                plugin_slug="aws",
                org_slug=org.slug,
                auto_discover_aws=True,
                aws_cluster_name="target",
            )
            rebind = await sync_to_async(ClusterWorkloadIdentityRebind.objects.get)()
            result = await asyncio.wait_for(
                temporal_env.client.get_workflow_handle(rebind.workflow_id).result(), 60
            )

        assert (rebind.issuer, rebind.previous_issuers) == (NEW, [OLD])
        assert result["state"] == "succeeded"
        assert result["outcomes"] == {"rebound": 3}
        for slug in world.moved:
            assert await sync_to_async(_trusts)(world.roles[slug], NEW, f"{slug}-production"), slug
        for slug in world.untouched:
            assert await sync_to_async(_trusts)(world.roles[slug], OLD, f"{slug}-production"), slug
    await sync_to_async(rebind.refresh_from_db)()
    assert (rebind.state, rebind.apps_total, rebind.apps_reconciled) == ("succeeded", 3, 3)
