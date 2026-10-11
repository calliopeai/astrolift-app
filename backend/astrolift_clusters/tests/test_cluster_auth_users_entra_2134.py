"""An Azure cluster's sign-in users live in its own Entra ID tenant (#2134)."""

from __future__ import annotations

import uuid

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from core.app_deploy import AppDeployError, driver_for_capability

pytestmark = pytest.mark.django_db

TENANT = "0f1e2d3c-4b5a-4968-8778-695a4b3c2d1e"
OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _cluster(*, discovery, tenant=TENANT):
    org = Organization.objects.create(name="Veruus", slug=f"v-{uuid.uuid4().hex[:8]}")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="azure", defaults={"name": "azure", "capabilities_manifest": {}, "config_schema": {}}
    )
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:8]}",
        name="c",
        provider_plugin=plugin,
        provider_config={"tenant_id": tenant, "subscription_id": "s", "resource_group": "rg"},
        region="eastus",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        ingress_class="envoy",
        oidc_auth_config={"discovery_url": discovery, "client_id": "c", "auth_proxy_host": "auth.x"},
    )


def test_central_auth_in_the_clusters_own_tenant_is_managed_through_entra():
    cluster = _cluster(discovery=f"https://login.microsoftonline.com/{TENANT}/v2.0")
    driver = driver_for_capability(cluster, "identity_users")
    assert driver.provider == "Microsoft Entra ID" and driver.pool_id == TENANT


@pytest.mark.parametrize(
    "discovery,reason",
    [
        ("https://login.example.net/oidc", "does not sign in through Microsoft Entra ID"),
        ("https://login.microsoftonline.com/common/v2.0", "does not sign in through Microsoft Entra ID"),
        (f"https://login.microsoftonline.com/{OTHER}/v2.0", "not the tenant this cluster runs in"),
    ],
)
def test_another_idp_or_tenant_is_refused_with_its_reason(discovery, reason):
    with pytest.raises(AppDeployError, match=reason):
        driver_for_capability(_cluster(discovery=discovery), "identity_users")
