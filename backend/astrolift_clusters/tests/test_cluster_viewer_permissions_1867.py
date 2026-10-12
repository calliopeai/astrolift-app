"""A cluster says which of its actions the viewer may take (#1867).

The settings screen used account-wide permissions, so an org admin saw
manage, decommission and unregister on a shared cluster the server refuses
them (#1918). ``viewerPermissions`` comes from the same checker as the
mutations, at the owning org, with shared-cluster writes left to the operator.
"""

from __future__ import annotations

import uuid

import pytest

from astrolift_clusters.models import ProviderPlugin
from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_clusters.tests.test_heartbeat import _make_cluster
from astrolift_identity.models import Member
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)

QUERY = "query($s:String!){astroliftCluster(slug:$s){slug viewerPermissions}}"
WRITES = ["cluster.manage", "cluster.unregister", "cluster.update", "cluster.users"]


def _viewer(world, permissions):
    user = make_user("v" + uuid.uuid4().hex[:6])
    Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(user, permissions=permissions, kind="ORG", scope_id=world.org.pk, slug=uuid.uuid4().hex)
    return http_token(world, scopes=("admin",), actor=user)[1]


def test_owned_clusters_offer_what_the_viewer_holds_and_shared_ones_only_read(client):
    world = ScopeWorld("cvp-" + uuid.uuid4().hex[:8])
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s", slug=f"k8s-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    owned = _make_cluster(world.org, plugin)
    shared = _make_cluster(None, plugin)
    admin = _viewer(world, [Permission.ORG_READ, Permission.CLUSTER_READ, *map(Permission, WRITES)])
    reader = _viewer(world, [Permission.ORG_READ, Permission.CLUSTER_READ])

    def perms(headers, cluster):
        return graphql_http(client, headers, QUERY, {"s": cluster.slug})["data"]["astroliftCluster"][
            "viewerPermissions"
        ]

    assert perms(admin, owned) == sorted(["cluster.read", *WRITES])
    assert perms(admin, shared) == ["cluster.read"]
    assert perms(reader, owned) == ["cluster.read"]
