"""Cluster inventory reads are separate from registration (#2295).

A read-only bearer (``read:clusters``, the mobile default) or an org viewer
reaches the cluster list and one exact cluster through ``cluster.read``. The
endpoint, edge-auth configuration, management error and bootstrap history
stay with ``cluster.register``, and the read grant opens no write.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager

import pytest
from django.utils import timezone

from astrolift_clusters.models import ClusterBootstrapRun, TenantCluster
from astrolift_clusters.schema import mutations as mut
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_graphql import GUID
from astrolift_identity.api_tokens import (
    DEFAULT_SCOPES,
    reset_current_api_token,
    set_current_api_token,
    token_scope_allows_permission,
)
from astrolift_identity.models import ApiToken, Member, Organization
from astrolift_identity.system_roles import SYSTEM_ROLES
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db

ALB_AUTH = {"user_pool_arn": "arn:aws:cognito-idp:us-west-2:1:userpool/x", "user_pool_client_id": "c"}


@pytest.fixture
def world():
    world = ScopeWorld("cluster-read-2295")
    world.cluster = make_cluster(world, "cluster-read-2295")
    TenantCluster.all_objects.filter(pk=world.cluster.pk).update(
        endpoint="https://api.k8s.internal",
        alb_auth_config=ALB_AUTH,
        last_management_error="AccessDenied on role arn:aws:iam::1:role/x",
    )
    world.cluster.refresh_from_db()
    now = timezone.now()
    ClusterBootstrapRun.objects.create(
        tenant_cluster=world.cluster,
        organization=world.org,
        status="succeeded",
        chart_version="1",
        installed_releases=[],
        cli_version="1",
        host_info={},
        started_at=now - dt.timedelta(seconds=1),
        ended_at=now,
    )
    world.user = make_user("cluster-read-2295")
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
def _tenant(world):
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        yield


@contextmanager
def _token(world, scopes):
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="read2295",
        token_hash=uuid.uuid4().hex,
        scopes=list(scopes),
    )
    handle = set_current_api_token(token)
    try:
        yield token
    finally:
        reset_current_api_token(handle)


def _reads(world):
    info = make_info(world.user)
    query = ClustersQuery()
    page = query.astrolift_clusters_page(info)
    exact = query.astrolift_cluster(info, slug=world.cluster.slug)
    listed = query.astrolift_clusters(info)
    return page.items, exact, listed


def _assert_inventory_only(cluster):
    assert cluster.slug
    assert cluster.endpoint == ""
    assert cluster.alb_auth_config is None
    assert cluster.oidc_auth_config is None
    assert cluster.last_management_error == ""
    assert cluster.last_bootstrap_run() is None
    assert cluster.bootstrap_runs() == []


def test_read_clusters_scope_reaches_cluster_read_but_no_registration_or_writes():
    token = ApiToken(scopes=list(DEFAULT_SCOPES))
    assert "read:clusters" in DEFAULT_SCOPES
    assert token_scope_allows_permission(token, Permission.CLUSTER_READ.value)
    for permission in (
        Permission.CLUSTER_REGISTER,
        Permission.CLUSTER_UPDATE,
        Permission.CLUSTER_MANAGE,
        Permission.CLUSTER_UNREGISTER,
        Permission.CLUSTER_USERS,
    ):
        assert not token_scope_allows_permission(token, permission.value)


def test_mobile_default_bearer_of_an_org_admin_pages_and_reads_without_operator_fields(world):
    """The reported shape: an admin-capable user, but the mobile bearer ceiling."""
    _grant(world, Permission.CLUSTER_READ, Permission.CLUSTER_REGISTER, Permission.CLUSTER_UPDATE)
    with _tenant(world), _token(world, DEFAULT_SCOPES):
        items, exact, listed = _reads(world)
        assert [c.slug for c in items] == [world.cluster.slug]
        assert [c.slug for c in listed] == [world.cluster.slug]
        for cluster in (*items, exact, *listed):
            _assert_inventory_only(cluster)


def test_mobile_default_bearer_cannot_register_update_or_manage(world):
    _grant(world, *Permission)
    cluster_id = GUID(str(world.cluster.guid))
    before = list(TenantCluster.all_objects.order_by("pk").values())
    with _tenant(world), _token(world, DEFAULT_SCOPES):
        mutation = mut.ClustersMutation()
        info = make_info(world.user)
        results = [
            mutation.register_tenant_cluster(
                info,
                input=mut.RegisterTenantClusterInput(
                    slug="new-2295",
                    name="New",
                    provider_plugin_slug=world.cluster.provider_plugin.slug,
                    auth_method="kubeconfig",
                ),
            ),
            mutation.update_tenant_cluster(
                info, input=mut.UpdateTenantClusterInput(id=cluster_id, region="elsewhere")
            ),
            mutation.issue_cluster_agent_key(
                info, input=mut.IssueClusterAgentKeyInput(cluster_id=cluster_id)
            ),
            mutation.unregister_tenant_cluster(info, input=mut.UnregisterTenantClusterInput(id=cluster_id)),
        ]
    assert all(not r.ok and r.errors[0].code == "PERMISSION_DENIED" for r in results), results
    assert list(TenantCluster.all_objects.order_by("pk").values()) == before


def test_org_viewer_reads_inventory_without_operator_fields(world):
    viewer = next(perms for slug, _, _, _, perms in SYSTEM_ROLES if slug == "org_viewer")
    assert Permission.CLUSTER_READ in viewer and Permission.CLUSTER_REGISTER not in viewer
    _grant(world, *viewer)
    with _tenant(world):
        items, exact, _ = _reads(world)
        for cluster in (*items, exact):
            _assert_inventory_only(cluster)


def test_registration_authority_still_sees_operator_fields(world):
    _grant(world, Permission.CLUSTER_READ, Permission.CLUSTER_REGISTER)
    with _tenant(world):
        items, exact, _ = _reads(world)
        for cluster in (*items, exact):
            assert cluster.endpoint == "https://api.k8s.internal"
            assert cluster.alb_auth_config == ALB_AUTH
            assert cluster.last_management_error.startswith("AccessDenied")
            assert cluster.last_bootstrap_run() is not None
            assert len(cluster.bootstrap_runs()) == 1


def test_cluster_owner_role_carries_cluster_read():
    owner = next(perms for slug, _, _, _, perms in SYSTEM_ROLES if slug == "cluster_owner")
    assert Permission.CLUSTER_READ in owner


def test_without_cluster_read_the_inventory_stays_refused(world):
    _grant(world, Permission.APP_READ, Permission.PROVIDER_PLUGIN_READ)
    with _tenant(world), pytest.raises(PermissionDenied):
        ClustersQuery().astrolift_clusters_page(make_info(world.user))


def test_other_org_cluster_is_not_visible_to_a_read_grant(world):
    other = Organization.objects.create(name="Other", slug="other-2295")
    TenantCluster.all_objects.filter(pk=world.cluster.pk).update(organization=other)
    _grant(world, Permission.CLUSTER_READ)
    with _tenant(world):
        assert ClustersQuery().astrolift_clusters_page(make_info(world.user)).items == []
        assert ClustersQuery().astrolift_cluster(make_info(world.user), slug=world.cluster.slug) is None
