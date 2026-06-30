"""Tests for the provider picker queries on ``ClustersQuery`` (#858-#861).

These resolvers no longer touch ``boto3`` directly. Each one delegates to
a dispatch helper that resolves the right provider / DNS driver and
returns plain dicts, so the resolver layer is a thin permission-gated map
from dispatch payloads onto the GraphQL types:

* ``astroliftProviderRegions(providerPluginSlug)`` (#860) — region picker
  on the cluster-register dialog; dispatches
  ``core.cluster_management.provider_regions_dispatch``.
* ``astroliftCognitoUserPools(clusterId)`` (#859) — user-pool picker on
  the ingress auth-gate card; dispatches
  ``core.cluster_management.cognito_user_pools_dispatch``.
* ``astroliftClusterCertificates(clusterId)`` (#858) — cert picker on the
  app Domains page; dispatches
  ``core.cluster_management.cluster_certificates_dispatch``.
* ``astroliftDnsZones(dnsDriver)`` (#861) + ``astroliftDnsCertificates``
  (#858) — zone + cert pickers on the "Add managed domain" dialog;
  dispatch ``core.dns_discovery.dns_zones_dispatch`` /
  ``dns_certificates_dispatch``.

The dispatch helpers are imported *inside* each resolver, so they're
patched on their defining module (``core.cluster_management.X_dispatch`` /
``core.dns_discovery.Y_dispatch``). Per resolver we assert the real
dict→type mapping (happy path), the degrade-to-empty / degrade-to-
unsupported semantics when dispatch raises ``ClusterManagementError``, and
the deny-by-default permission gate.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- fixtures ---------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
    )


@pytest.fixture
def plugin():
    # bulk_create skips BaseCoreModel.save (numeric version-increment vs
    # CharField), matching the count-query / status-tab suites.
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="aws",
                slug=f"aws-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
    )
    return row


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={"region": "us-west-2"},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ingress_class="alb",
    )


def _granted_query(permission_resolver, permission):
    permission_resolver.grant(permission)
    return ClustersQuery()


# ---- provider_regions (#860) -----------------------------------------


def test_provider_regions_happy_path(permission_resolver, org):
    """Dispatch dicts map 1:1 onto ``ProviderRegionType`` rows."""
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    rows = [
        {"id": "us-east-1", "label": "US East (N. Virginia)", "continent": "North America"},
        {"id": "eu-west-1", "label": "EU (Ireland)", "continent": "Europe"},
    ]
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.cluster_management.provider_regions_dispatch",
            return_value=rows,
        ) as mock_dispatch:
            result = q.astrolift_provider_regions(_info(), provider_plugin_slug="aws")

    mock_dispatch.assert_called_once_with(provider_plugin_slug="aws")
    assert [(r.id, r.label, r.continent) for r in result] == [
        ("us-east-1", "US East (N. Virginia)", "North America"),
        ("eu-west-1", "EU (Ireland)", "Europe"),
    ]


def test_provider_regions_dispatch_error_returns_empty(permission_resolver, org):
    """Driver-resolution failure (``ClusterManagementError``) degrades to
    an empty list so the picker falls back to free-text entry."""
    from core.cluster_management import ClusterManagementError

    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.cluster_management.provider_regions_dispatch",
            side_effect=ClusterManagementError("plugin not loaded"),
        ):
            result = q.astrolift_provider_regions(_info(), provider_plugin_slug="k8s_native")
    assert result == []


def test_provider_regions_denied_without_cluster_register():
    """Deny-by-default: no grant -> ``PermissionDenied`` before any
    dispatch fires."""
    with pytest.raises(PermissionDenied):
        ClustersQuery().astrolift_provider_regions(_info(), provider_plugin_slug="aws")


# ---- cognito_user_pools (#859) ---------------------------------------


def test_cognito_user_pools_happy_path(permission_resolver, cluster, org):
    """Dispatch dicts map 1:1 onto ``CognitoUserPoolType`` rows."""
    q = _granted_query(permission_resolver, Permission.CLUSTER_UPDATE)
    rows = [
        {
            "pool_id": "us-west-2_aaa",
            "pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_aaa",
            "name": "customers",
            "domain": "customers-auth",
            "region": "us-west-2",
        },
    ]
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.cluster_management.cognito_user_pools_dispatch",
            return_value=rows,
        ):
            result = q.astrolift_cognito_user_pools(_info(), cluster_id=GUID(str(cluster.guid)))

    assert len(result) == 1
    pool = result[0]
    assert pool.pool_id == "us-west-2_aaa"
    assert pool.pool_arn == "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_aaa"
    assert pool.name == "customers"
    assert pool.domain == "customers-auth"
    assert pool.region == "us-west-2"


def test_cognito_user_pools_missing_cluster_returns_empty(permission_resolver, org):
    """A non-existent cluster guid returns an empty list (no raise) — the
    card renders empty and the operator can still type values."""
    q = _granted_query(permission_resolver, Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = q.astrolift_cognito_user_pools(_info(), cluster_id=GUID(str(uuid.uuid4())))
    assert result == []


def test_cognito_user_pools_dispatch_error_returns_empty(permission_resolver, cluster, org):
    """Driver / credential failure (``ClusterManagementError``) yields an
    empty list so the picker degrades to free-entry."""
    from core.cluster_management import ClusterManagementError

    q = _granted_query(permission_resolver, Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.cluster_management.cognito_user_pools_dispatch",
            side_effect=ClusterManagementError("no creds"),
        ):
            result = q.astrolift_cognito_user_pools(_info(), cluster_id=GUID(str(cluster.guid)))
    assert result == []


def test_cognito_user_pools_denied_without_cluster_update(cluster, org, permission_resolver):
    """The auth-gate save path is ``cluster.update``-gated; the pool
    picker shares that permission. No grant -> denied."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cognito_user_pools(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
            )


# ---- cluster_certificates (#858) -------------------------------------


def test_cluster_certificates_happy_path(permission_resolver, cluster, org):
    """Dispatch returns a supported payload; the resolver maps each cert
    1:1 onto ``ClusterCertificateType`` and reports ``supported=True``."""
    q = _granted_query(permission_resolver, Permission.APP_DEPLOY)
    payload = {
        "supported": True,
        "certificates": [
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/abc",
                "name": "api.acme.example",
                "domain_name": "api.acme.example",
                "status": "ISSUED",
            },
        ],
    }
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.cluster_management.cluster_certificates_dispatch",
            return_value=payload,
        ):
            result = q.astrolift_cluster_certificates(_info(), cluster_id=GUID(str(cluster.guid)))

    assert result.supported is True
    assert len(result.certificates) == 1
    cert = result.certificates[0]
    assert cert.arn == "arn:aws:acm:us-east-1:123456789012:certificate/abc"
    assert cert.name == "api.acme.example"
    assert cert.domain_name == "api.acme.example"
    assert cert.status == "ISSUED"


def test_cluster_certificates_missing_cluster_unsupported(permission_resolver, org):
    """A non-existent cluster guid -> ``supported=False`` (empty list),
    not a raise. The form falls back to the free-text ARN field."""
    q = _granted_query(permission_resolver, Permission.APP_DEPLOY)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = q.astrolift_cluster_certificates(_info(), cluster_id=GUID(str(uuid.uuid4())))
    assert result.supported is False
    assert result.certificates == []


def test_cluster_certificates_dispatch_error_keeps_supported(permission_resolver, cluster, org):
    """A supported driver whose cloud call blows up raises
    ``ClusterManagementError`` from dispatch. The resolver keeps
    ``supported=True`` with an empty list — the capability exists, the
    data just isn't reachable, so the picker shows an empty state rather
    than silently reverting to manual entry."""
    from core.cluster_management import ClusterManagementError

    q = _granted_query(permission_resolver, Permission.APP_DEPLOY)
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.cluster_management.cluster_certificates_dispatch",
            side_effect=ClusterManagementError("AccessDenied"),
        ):
            result = q.astrolift_cluster_certificates(_info(), cluster_id=GUID(str(cluster.guid)))
    assert result.supported is True
    assert result.certificates == []


def test_cluster_certificates_denied_without_app_deploy(cluster, org, permission_resolver):
    """The cert picker is gated on ``app.deploy`` — the same permission
    the Domains page's mutations use. No grant -> denied."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cluster_certificates(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
            )


# ---- dns_certificates (#858, dialog cert picker) ---------------------


def test_dns_certificates_happy_path(permission_resolver, org):
    """The dialog cert picker has no cluster context; it dispatches by
    DNS-driver slug. Dispatch dicts map 1:1 onto ``ClusterCertificateType``
    with ``supported=True``."""
    q = _granted_query(permission_resolver, Permission.PROVIDER_PLUGIN_READ)
    payload = {
        "supported": True,
        "certificates": [
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/abc",
                "name": "acme.example",
                "domain_name": "acme.example",
                "status": "ISSUED",
            },
        ],
    }
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.dns_discovery.dns_certificates_dispatch",
            return_value=payload,
        ) as mock_dispatch:
            result = q.astrolift_dns_certificates(_info(), dns_driver="route53")

    mock_dispatch.assert_called_once_with(dns_driver="route53")
    assert result.supported is True
    assert len(result.certificates) == 1
    assert result.certificates[0].arn == "arn:aws:acm:us-east-1:123456789012:certificate/abc"
    assert result.certificates[0].status == "ISSUED"


def test_dns_certificates_unsupported_driver(permission_resolver, org):
    """A driver without cert discovery wired (cloud_dns / azure_dns today)
    -> ``supported=False`` so the UI falls back to free-text entry."""
    q = _granted_query(permission_resolver, Permission.PROVIDER_PLUGIN_READ)
    payload = {"supported": False, "certificates": []}
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.dns_discovery.dns_certificates_dispatch",
            return_value=payload,
        ):
            result = q.astrolift_dns_certificates(_info(), dns_driver="azure_dns")
    assert result.supported is False
    assert result.certificates == []


def test_dns_certificates_denied_without_provider_plugin_read(org, permission_resolver):
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_dns_certificates(_info(), dns_driver="route53")


# ---- dns_zones (#861) ------------------------------------------------


def test_dns_zones_happy_path(permission_resolver, org):
    """Dispatch zone dicts map onto ``DnsZoneType`` with ``supported=True``.
    config_json is carried through verbatim for the dialog textarea
    auto-fill."""
    q = _granted_query(permission_resolver, Permission.PROVIDER_PLUGIN_READ)
    payload = {
        "supported": True,
        "zones": [
            {
                "id": "Z123ABC",
                "name": "acme.example.",
                "private": False,
                "config_json": '{"zone_id": "Z123ABC"}',
            },
            {
                "id": "Z999PRIV",
                "name": "internal.acme.",
                "private": True,
                "config_json": '{"zone_id": "Z999PRIV"}',
            },
        ],
    }
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.dns_discovery.dns_zones_dispatch",
            return_value=payload,
        ) as mock_dispatch:
            result = q.astrolift_dns_zones(_info(), dns_driver="route53")

    mock_dispatch.assert_called_once_with(dns_driver="route53")
    assert result.supported is True
    assert [(z.id, z.name, z.private, z.config_json) for z in result.zones] == [
        ("Z123ABC", "acme.example.", False, '{"zone_id": "Z123ABC"}'),
        ("Z999PRIV", "internal.acme.", True, '{"zone_id": "Z999PRIV"}'),
    ]


def test_dns_zones_unsupported_driver(permission_resolver, org):
    """A driver without zone discovery wired -> ``supported=False`` so the
    UI disables the picker and leaves the textarea editable."""
    q = _granted_query(permission_resolver, Permission.PROVIDER_PLUGIN_READ)
    payload = {"supported": False, "zones": []}
    with tenant_context(TenantContext(organization_id=org.id)):
        with patch(
            "core.dns_discovery.dns_zones_dispatch",
            return_value=payload,
        ):
            result = q.astrolift_dns_zones(_info(), dns_driver="cloud_dns")
    assert result.supported is False
    assert result.zones == []


def test_dns_zones_denied_without_provider_plugin_read(org, permission_resolver):
    """Zone picker is gated on ``provider_plugin.read`` (the managed-
    domains admin surface). No grant -> denied."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_dns_zones(_info(), dns_driver="route53")
