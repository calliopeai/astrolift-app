"""``setIngressCutover`` records an ingress-nginx cutover, operator only (#2117).

The recipe cleanup reads the two fields it writes: the releases to keep, and
whether the cutover is complete. ``test_install_prereqs.py`` covers the
cleanup itself.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.schema.mutations import ClustersMutation, SetIngressCutoverInput
from astrolift_graphql import GUID
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _set(org, actor, fake_info, cluster, **fields):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        return ClustersMutation().set_ingress_cutover(
            fake_info, input=SetIngressCutoverInput(cluster_id=GUID(str(cluster.guid)), **fields)
        )


def test_an_org_admin_who_is_not_the_platform_operator_is_refused(
    org, cluster, actor, fake_info, permission_resolver
):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)

    result = _set(org, actor, fake_info, cluster, complete=True)

    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    cluster.refresh_from_db()
    assert cluster.ingress_cutover_completed_at is None


def test_the_operator_preserves_releases_completes_and_rolls_back(
    org, cluster, actor, fake_info, permission_resolver
):
    actor.is_superuser = True
    actor.save(update_fields=["is_superuser"])
    permission_resolver.grant(Permission.CLUSTER_MANAGE)

    kept = _set(org, actor, fake_info, cluster, preserved_releases=["ingress-nginx", "ingress-nginx"])
    assert kept.ok, kept.errors
    assert kept.data.preserved_releases == ["ingress-nginx"]
    assert kept.data.completed_at is None

    done = _set(org, actor, fake_info, cluster, complete=True)
    assert done.data.completed_at is not None
    cluster.refresh_from_db()
    assert cluster.preserved_ingress_releases == ["ingress-nginx"]
    assert cluster.ingress_cutover_completed_at is not None

    rolled_back = _set(org, actor, fake_info, cluster, complete=False)
    assert rolled_back.data.completed_at is None


def test_release_names_are_validated(org, cluster, actor, fake_info, permission_resolver):
    actor.is_superuser = True
    actor.save(update_fields=["is_superuser"])
    permission_resolver.grant(Permission.CLUSTER_MANAGE)

    result = _set(org, actor, fake_info, cluster, preserved_releases=["Not A Release!"])

    assert result.ok is False
    assert result.errors[0].field == "preservedReleases"
