"""A re-pointed cluster row moves its per-app roles to the new OIDC issuer (#2472).

The installer's region change restores Astrolift's database in the target
region and registers the target's first cluster under the old region's slug,
so ``register_tenant_cluster`` upserts the old row with the new cluster's
endpoint and issuer. Every per-app IRSA role minted for the row still trusts
the old cluster's OIDC provider. These tests drive the command the installer
runs and check what it records and starts; the move itself is covered in
``astrolift_workflows/tests/test_rebind_cluster_workload_identity_2472.py``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from astrolift_clusters.management.commands import register_tenant_cluster as cmd
from astrolift_clusters.models import ClusterWorkloadIdentityRebind, ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_clusters.schema.types import cluster_to_type
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.workload_identity_rebind import (
    cluster_issuers,
    current_issuer,
    normalize_issuer,
    request_workload_identity_rebind,
)
from astrolift_identity.models import Member
from core.app_deploy import _config_for_capability
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

SLUG = "acme-astrolift"
ACCOUNT = "123456789012"
OLD = "oidc.eks.us-east-1.amazonaws.com/id/OLD"
MID = "oidc.eks.eu-west-1.amazonaws.com/id/MID"
NEW = "oidc.eks.us-west-2.amazonaws.com/id/NEW"


@pytest.fixture
def aws_plugin():
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}}
    )
    return plugin


@pytest.fixture
def starts(monkeypatch):
    """Every workflow start, with what it asked Temporal for."""
    calls: list[dict[str, Any]] = []
    failures: list[Exception] = []

    def start_workflow(name, args, *, workflow_id, task_queue=None, replace_running=True):
        if failures:
            raise failures.pop(0)
        calls.append(
            {"name": name, "args": args, "workflow_id": workflow_id, "replace_running": replace_running}
        )
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id="run", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start_workflow)
    return calls, failures


def _discovers(monkeypatch, issuer: str, *, region: str = "us-west-2") -> None:
    def discover(cluster_name, region_):
        return {
            "endpoint": f"https://{cluster_name}.eks.invalid",
            "ca_cert": "Y2E=",
            "account_id": ACCOUNT,
            "oidc_issuer_url": f"https://{issuer}",
            "oidc_provider_arn": f"arn:aws:iam::{ACCOUNT}:oidc-provider/{issuer}",
        }

    monkeypatch.setattr(cmd, "_discover_aws", discover)
    monkeypatch.setenv("AWS_REGION", region)


def _register(**kwargs) -> TenantCluster:
    call_command(
        "register_tenant_cluster",
        slug=SLUG,
        plugin_slug="aws",
        auto_discover_aws=True,
        aws_cluster_name=kwargs.pop("aws_cluster_name", "target-cluster"),
        **kwargs,
    )
    return TenantCluster.all_objects.get(slug=SLUG)


def _old_region_row(aws_plugin, *, auth_issuer: str | None = OLD) -> TenantCluster:
    """The row the restored database holds: the old region's first cluster."""
    auth = {"cluster_name": SLUG, "region": "us-east-1"}
    if auth_issuer:
        auth["cluster_oidc_issuer"] = auth_issuer
    return TenantCluster.objects.create(
        slug=SLUG,
        name=SLUG,
        provider_plugin=aws_plugin,
        auth_method="exec_plugin",
        region="us-east-1",
        endpoint="https://old.eks.invalid",
        auth_config=auth,
        provider_config={"account_id": ACCOUNT, "region": "us-east-1", "cluster_oidc_issuer": OLD},
        ingress_class="alb",
    )


def _rebinds() -> list[ClusterWorkloadIdentityRebind]:
    return list(ClusterWorkloadIdentityRebind.objects.order_by("pk"))


def test_a_re_pointed_row_records_a_rebind_and_starts_its_workflow(aws_plugin, monkeypatch, starts):
    calls, _ = starts
    _old_region_row(aws_plugin)
    _discovers(monkeypatch, NEW)

    cluster = _register()

    [rebind] = _rebinds()
    assert (rebind.tenant_cluster_id, rebind.issuer, rebind.previous_issuers) == (cluster.pk, NEW, [OLD])
    assert rebind.state == ClusterWorkloadIdentityRebind.State.PENDING
    [call] = calls
    assert call["name"] == "RebindClusterWorkloadIdentityWorkflow"
    assert call["workflow_id"] == rebind.workflow_id == f"RebindClusterWorkloadIdentityWorkflow-{rebind.guid}"
    # Joins a run in flight and retries a failed one instead of cutting it off.
    assert call["replace_running"] is False
    assert call["args"][0].rebind_id == rebind.pk
    assert call["args"][0].actor.kind == "system"


def test_the_re_pointed_row_hands_the_identity_driver_the_new_issuer(aws_plugin, monkeypatch, starts):
    """The IRSA config reads ``auth_config``, which the command used to replace
    without the issuer, so the driver held none until a deploy rediscovered it."""
    _old_region_row(aws_plugin)
    _discovers(monkeypatch, NEW)

    cluster = _register()

    assert cluster.auth_config["cluster_oidc_issuer"] == NEW
    assert cluster.provider_config["cluster_oidc_issuer"] == NEW
    assert _config_for_capability("aws", cluster, "identity").cluster_oidc_issuer == NEW


@pytest.mark.parametrize("auth_issuer", [OLD, None], ids=["cached", "dropped-by-an-earlier-start"])
def test_registering_the_same_cluster_again_records_nothing(aws_plugin, monkeypatch, starts, auth_issuer):
    """What every container start does."""
    calls, _ = starts
    _old_region_row(aws_plugin, auth_issuer=auth_issuer)
    _discovers(monkeypatch, OLD, region="us-east-1")

    _register(aws_cluster_name=SLUG)

    assert _rebinds() == []
    assert calls == []


def test_the_first_registration_records_nothing(aws_plugin, monkeypatch, starts):
    calls, _ = starts
    _discovers(monkeypatch, NEW)

    _register()

    assert _rebinds() == []
    assert calls == []


def test_a_failed_start_stays_pending_and_the_next_registration_starts_it(aws_plugin, monkeypatch, starts):
    """The command runs at every control-plane start, possibly before
    Temporal answers. It must not fail, and the move must not be lost."""
    calls, failures = starts
    failures.append(RuntimeError("temporal unreachable"))
    _old_region_row(aws_plugin)
    _discovers(monkeypatch, NEW)

    _register()

    [rebind] = _rebinds()
    assert rebind.state == ClusterWorkloadIdentityRebind.State.PENDING
    assert "temporal unreachable" in rebind.error
    assert calls == []

    _register()

    assert _rebinds() == [rebind]
    [call] = calls
    assert call["workflow_id"] == rebind.workflow_id


def test_the_re_point_and_its_request_commit_together(aws_plugin, monkeypatch, starts):
    """A registration that dies after the upsert must not leave the row on the
    new issuer with no request: every later registration would compare the
    new issuer with itself and the roles would never move."""
    calls, _ = starts
    _old_region_row(aws_plugin)
    _discovers(monkeypatch, NEW)

    def dies(**kwargs):
        raise RuntimeError("server closed the connection unexpectedly")

    with monkeypatch.context() as patched:
        patched.setattr(ClusterWorkloadIdentityRebind.objects, "create", dies)
        with pytest.raises(RuntimeError, match="server closed the connection"):
            _register()

    assert cluster_issuers(TenantCluster.all_objects.get(slug=SLUG)) == {OLD}
    assert calls == []

    _register()

    [rebind] = _rebinds()
    assert (rebind.issuer, rebind.previous_issuers) == (NEW, [OLD])
    assert [call["workflow_id"] for call in calls] == [rebind.workflow_id]


@pytest.mark.parametrize(
    ("state", "started_again"),
    [
        (ClusterWorkloadIdentityRebind.State.RUNNING, True),
        (ClusterWorkloadIdentityRebind.State.FAILED, True),
        (ClusterWorkloadIdentityRebind.State.SUCCEEDED, False),
    ],
)
def test_an_unfinished_request_is_started_again_and_a_finished_one_is_not(
    aws_plugin, monkeypatch, starts, state, started_again
):
    calls, _ = starts
    cluster = _old_region_row(aws_plugin, auth_issuer=NEW)
    cluster.provider_config = {**cluster.provider_config, "cluster_oidc_issuer": NEW}
    cluster.save()
    rebind = ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=cluster, issuer=NEW, previous_issuers=[OLD], state=state
    )
    _discovers(monkeypatch, NEW)

    _register()

    assert _rebinds() == [rebind]
    assert [call["workflow_id"] for call in calls] == ([rebind.workflow_id] if started_again else [])


def test_a_second_move_before_the_first_finished_carries_its_issuers(aws_plugin, monkeypatch, starts):
    """Roles the first request had not reached still trust the first issuer."""
    calls, failures = starts
    failures.append(RuntimeError("temporal unreachable"))
    _old_region_row(aws_plugin)
    _discovers(monkeypatch, MID)
    _register()
    _discovers(monkeypatch, NEW)

    _register()

    first, second = _rebinds()
    assert (first.issuer, first.previous_issuers) == (MID, [OLD])
    assert (second.issuer, second.previous_issuers) == (NEW, sorted([OLD, MID]))
    assert [call["workflow_id"] for call in calls] == [second.workflow_id]


def test_a_move_after_a_finished_one_starts_from_the_row_alone(aws_plugin, monkeypatch, starts):
    cluster = _old_region_row(aws_plugin, auth_issuer=MID)
    cluster.provider_config = {**cluster.provider_config, "cluster_oidc_issuer": MID}
    cluster.save()
    ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=cluster,
        issuer=MID,
        previous_issuers=[OLD],
        state=ClusterWorkloadIdentityRebind.State.SUCCEEDED,
    )
    _discovers(monkeypatch, NEW)

    _register()

    latest = _rebinds()[-1]
    assert (latest.issuer, latest.previous_issuers) == (NEW, [MID])


@pytest.mark.parametrize("slug", ["gcp", "azure", "k8s_native"])
def test_other_providers_record_nothing(starts, slug):
    """GKE Workload Identity binds no cluster issuer; AKS federated
    credentials are written by an identity path the deploy reconciles."""
    calls, _ = starts
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug=slug, defaults={"name": slug, "capabilities_manifest": {}, "config_schema": {}}
    )
    cluster = TenantCluster.objects.create(
        slug=f"{slug}-2472",
        name=slug,
        provider_plugin=plugin,
        auth_method="exec_plugin",
        auth_config={"cluster_oidc_issuer": NEW},
    )

    assert request_workload_identity_rebind(cluster, {OLD}) is None
    assert _rebinds() == []
    assert calls == []


def test_issuers_compare_without_scheme_or_trailing_slash():
    row = TenantCluster(
        auth_config={"cluster_oidc_issuer": f"https://{OLD}/"},
        provider_config={"cluster_oidc_issuer": OLD},
    )

    assert normalize_issuer(f" https://{NEW}/ ") == NEW
    assert cluster_issuers(row) == {OLD}
    assert current_issuer(TenantCluster(auth_config={}, provider_config={"cluster_oidc_issuer": NEW})) == NEW
    assert current_issuer(TenantCluster(auth_config={}, provider_config={})) == ""


# ---- the cluster read --------------------------------------------------------


@pytest.fixture
def world(aws_plugin):
    world = ScopeWorld("rebind-2472")
    world.cluster = TenantCluster.objects.create(
        organization=world.org,
        slug="prod-rebind-2472",
        name="prod",
        provider_plugin=aws_plugin,
        auth_method="exec_plugin",
        auth_config={"cluster_oidc_issuer": NEW},
    )
    world.user = make_user("rebind-2472")
    Member.objects.get_or_create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    return world


def _grant(world, *permissions):
    bind_role(
        world.user,
        permissions=permissions,
        kind="ORG",
        scope_id=world.org.pk,
        slug=f"grant-{uuid.uuid4().hex}",
    )


@contextmanager
def _tenant(world, user=None):
    user = user or world.user
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=user.pk)):
        yield


def _read(world, user=None):
    info = make_info(user or world.user)
    cluster = ClustersQuery().astrolift_cluster(info, slug=world.cluster.slug)
    return cluster.workload_identity_rebind(info)


def test_the_cluster_read_carries_the_latest_rebind(world):
    ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=world.cluster, issuer=MID, previous_issuers=[OLD], state="failed"
    )
    ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=world.cluster,
        issuer=NEW,
        previous_issuers=[MID, OLD],
        state="running",
        apps_total=3,
        apps_reconciled=1,
        failures=[{"app": "acme/web", "role": "astrolift-acme-web", "error": "AccessDenied"}],
    )
    _grant(world, Permission.CLUSTER_READ, Permission.CLUSTER_REGISTER)

    with _tenant(world):
        rebind = _read(world)

    assert (rebind.state, rebind.issuer, rebind.previous_issuers) == ("running", NEW, [MID, OLD])
    assert (rebind.apps_total, rebind.apps_reconciled) == (3, 1)
    assert [(f.app, f.role, f.error) for f in rebind.failures] == [
        ("acme/web", "astrolift-acme-web", "AccessDenied")
    ]


def test_a_cluster_never_re_pointed_reads_null(world):
    _grant(world, Permission.CLUSTER_READ, Permission.CLUSTER_REGISTER)
    with _tenant(world):
        assert _read(world) is None


def test_a_leaked_parent_does_not_reach_another_orgs_rebind(world, aws_plugin):
    """Re-scoped by caller org, as the bootstrap history is (#1183): a parent
    carrying another organization's cluster must not widen into its move,
    whose failures name that organization's apps."""
    other = ScopeWorld("rebind-2472-other")
    theirs = TenantCluster.objects.create(
        organization=other.org,
        slug="theirs-rebind-2472",
        name="theirs",
        provider_plugin=aws_plugin,
        auth_method="exec_plugin",
        auth_config={"cluster_oidc_issuer": NEW},
    )
    ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=theirs,
        issuer=NEW,
        previous_issuers=[OLD],
        failures=[{"app": f"{other.org.slug}/api", "role": "astrolift-other-api", "error": "AccessDenied"}],
    )
    _grant(world, Permission.CLUSTER_READ, Permission.CLUSTER_REGISTER)

    with _tenant(world):
        assert cluster_to_type(theirs).workload_identity_rebind(make_info(world.user)) is None


def test_inventory_readers_do_not_see_it(world):
    """Like the management error: it names roles and carries cloud errors."""
    ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=world.cluster, issuer=NEW, previous_issuers=[OLD]
    )
    _grant(world, Permission.CLUSTER_READ)
    with _tenant(world):
        assert _read(world) is None


def test_a_shared_clusters_rebind_is_the_platform_operators_alone(world):
    """A shared cluster's failures name apps across organizations; an org
    admin holds cluster.register at their org, which must not reveal them."""
    TenantCluster.all_objects.filter(pk=world.cluster.pk).update(organization=None)
    ClusterWorkloadIdentityRebind.objects.create(
        tenant_cluster=world.cluster,
        issuer=NEW,
        previous_issuers=[OLD],
        failures=[{"app": "other-org/api", "role": "astrolift-other-org-api", "error": "AccessDenied"}],
    )
    _grant(world, Permission.CLUSTER_READ, Permission.CLUSTER_REGISTER)
    with _tenant(world):
        assert _read(world) is None

    operator = get_user_model().objects.create(
        username="operator-2472", email="operator-2472@acme.test", is_superuser=True, is_staff=True
    )
    with _tenant(world, operator):
        rebind = _read(world, operator)
    assert rebind is not None
    assert [f.app for f in rebind.failures] == ["other-org/api"]
