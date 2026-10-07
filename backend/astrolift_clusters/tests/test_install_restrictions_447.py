"""The console refuses what the install withholds (calliope-installer#447).

``ASTROLIFT_WITHHELD_CAPABILITIES`` unset is today's behaviour exactly; set,
cluster deletion and the withheld controllers are refused with the reason
before anything is enqueued or any driver is built.
"""

import pytest
from _sdk.cluster import BootstrapComponent

from astrolift_clusters.schema import mutations
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    DecommissionClusterInputType,
    InstallClusterPrereqsInputType,
)
from astrolift_clusters.schema.types import bootstrap_plan_to_type
from astrolift_graphql import GUID
from astrolift_identity.models import Member
from core.cluster_management import ClusterManagementError, teardown_cluster_dispatch
from core.install_restrictions import ENV_VAR
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *_: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *_: None))
    monkeypatch.delenv(ENV_VAR, raising=False)
    w = ScopeWorld("withheld447")
    w.user = make_user("withheld447")
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    w.cluster = make_cluster(w, "withheld447")
    w.cluster.lifecycle = "managed"
    w.cluster.save()
    bind_role(
        w.user,
        permissions=[Permission.CLUSTER_UNREGISTER, Permission.CLUSTER_MANAGE],
        kind="ORG",
        scope_id=w.org.pk,
        slug="withheld447",
    )
    w.queued = []
    monkeypatch.setattr(mutations, "start_workflow", lambda *args, **kwargs: w.queued.append((args, kwargs)))
    return w


def decommission(w, *, delete_cloud_infra):
    with tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk)):
        return ClustersMutation().decommission_cluster(
            make_info(w.user),
            DecommissionClusterInputType(
                cluster_id=GUID(str(w.cluster.guid)), delete_cloud_infra=delete_cloud_infra
            ),
        )


def install(w, components):
    with tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk)):
        return ClustersMutation().install_cluster_prereqs(
            make_info(w.user),
            InstallClusterPrereqsInputType(
                cluster_id=GUID(str(w.cluster.guid)), selected_components=components
            ),
        )


def test_deleting_the_cloud_cluster_is_refused_when_clusters_are_withheld(world, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "dns,databases,load_balancers,clusters")
    result = decommission(world, delete_cloud_infra=True)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert "Cluster lifecycle is withheld" in result.errors[0].message
    world.cluster.refresh_from_db()
    assert world.cluster.lifecycle == "managed"
    assert world.queued == []


def test_retiring_the_row_still_works_when_clusters_are_withheld(world, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "clusters")
    assert decommission(world, delete_cloud_infra=False).ok
    assert len(world.queued) == 1


def test_unset_deletes_the_cloud_cluster_as_before(world):
    assert decommission(world, delete_cloud_infra=True).ok
    assert len(world.queued) == 1


def test_teardown_refuses_before_building_a_driver(world, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "clusters")
    built = []
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: built.append(c))
    with pytest.raises(ClusterManagementError, match="Cluster lifecycle is withheld"):
        teardown_cluster_dispatch(cluster=world.cluster, delete_cloud_infra=True)
    assert built == []


@pytest.mark.parametrize(
    ("withheld", "component", "words"),
    [
        ("dns", "external-dns", "DNS is withheld"),
        ("load_balancers", "aws-load-balancer-controller", "Load balancers are withheld"),
    ],
)
def test_installing_a_withheld_controller_is_refused(world, monkeypatch, withheld, component, words):
    monkeypatch.setenv(ENV_VAR, withheld)
    result = install(world, ["cert-manager", component])
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert words in result.errors[0].message
    assert world.queued == []


def test_other_components_install_when_controllers_are_withheld(world, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "dns,load_balancers")
    assert install(world, ["cert-manager"]).ok
    assert len(world.queued) == 1


def test_unset_installs_the_controllers_as_before(world):
    assert install(world, ["external-dns", "aws-load-balancer-controller"]).ok


def test_the_recipe_offers_a_withheld_controller_with_its_reason(world, monkeypatch):
    components = [
        BootstrapComponent(key=key, title=key, default_enabled=True, rationale="")
        for key in ("external-dns", "aws-load-balancer-controller", "cert-manager")
    ]
    cluster = world.cluster
    assert all(c.withheld_reason is None for c in bootstrap_plan_to_type(cluster, components).components)

    monkeypatch.setenv(ENV_VAR, "dns")
    plan = {c.key: c.withheld_reason for c in bootstrap_plan_to_type(cluster, components).components}
    assert plan["external-dns"] and "DNS is withheld" in plan["external-dns"]
    assert plan["aws-load-balancer-controller"] is None
    assert plan["cert-manager"] is None
