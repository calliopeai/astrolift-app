"""Tests for provider picker queries (#858-#861).

Covers:
* astroliftProviderRegions (#860) — ec2.describe_regions
* astroliftProviderCerts (#858)   — acm.list_certificates + describe_certificate
* astroliftProviderCognitoPools (#859) — cognito-idp.list_user_pools + describe_user_pool
* astroliftProviderHostedZones (#861)  — route53.list_hosted_zones

Each resolver delegates to boto3 only for ``plugin_slug="aws"``; all
others return an empty list immediately. boto3 calls are mocked via
``unittest.mock.patch`` so no real AWS credentials are needed.

Happy-path tests assert the correct shape is surfaced. Error-path
tests assert that boto3 failures are swallowed and an empty list is
returned — the UI falls back to free-text in that case.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from astrolift_clusters.models import ProviderPlugin, ProviderPluginConfig
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_identity.models import Organization
from core.permissions import Permission
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
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def plugin_config(org, plugin):
    return ProviderPluginConfig.objects.create(
        name="aws-config",
        slug=f"aws-cfg-{uuid.uuid4().hex[:6]}",
        organization=org,
        provider_plugin=plugin,
        config={"region": "us-west-2", "account_id": "123456789012"},
    )


def _granted_query(permission_resolver, permission):
    permission_resolver.grant(permission)
    return ClustersQuery()


# ---- provider_regions (#860) -----------------------------------------


def test_provider_regions_non_aws_returns_empty(permission_resolver):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    result = q.astrolift_provider_regions(_info(), plugin_slug="gcp")
    assert result == []


def test_provider_regions_ec2_error_returns_empty(permission_resolver, plugin, plugin_config, org):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client") as mock_boto:
            mock_boto.return_value.describe_regions.side_effect = Exception("network error")
            result = q.astrolift_provider_regions(_info(), plugin_slug="aws")
    assert result == []


def test_provider_regions_happy_path(permission_resolver, plugin, plugin_config, org):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    mock_ec2 = MagicMock()
    mock_ec2.describe_regions.return_value = {
        "Regions": [
            {"RegionName": "us-east-1"},
            {"RegionName": "us-west-2"},
            {"RegionName": "eu-west-1"},
        ]
    }
    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client", return_value=mock_ec2):
            result = q.astrolift_provider_regions(_info(), plugin_slug="aws")

    assert len(result) == 3
    values = [r.value for r in result]
    # Sorted alphabetically by the resolver.
    assert values == sorted(values)
    assert "us-west-2" in values


def test_provider_regions_denied_without_cluster_register(permission_resolver):
    from core.permissions import PermissionDenied

    q = ClustersQuery()
    with pytest.raises(PermissionDenied):
        q.astrolift_provider_regions(_info(), plugin_slug="aws")


# ---- provider_certs (#858) -------------------------------------------


def test_provider_certs_non_aws_returns_empty(permission_resolver):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    result = q.astrolift_provider_certs(_info(), plugin_slug="gcp")
    assert result == []


def test_provider_certs_acm_error_returns_empty(permission_resolver, plugin, plugin_config, org):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client") as mock_boto:
            mock_boto.return_value.get_paginator.side_effect = Exception("acm unavailable")
            result = q.astrolift_provider_certs(_info(), plugin_slug="aws")
    assert result == []


def test_provider_certs_happy_path(permission_resolver, plugin, plugin_config, org):
    import datetime

    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    mock_acm = MagicMock()
    # Paginator returns one cert ARN.
    mock_page = {"CertificateSummaryList": [{"CertificateArn": "arn:aws:acm:us-west-2:123:certificate/abc"}]}
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [mock_page]
    mock_acm.get_paginator.return_value = mock_paginator
    not_after = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)
    mock_acm.describe_certificate.return_value = {
        "Certificate": {
            "CertificateArn": "arn:aws:acm:us-west-2:123:certificate/abc",
            "DomainName": "*.example.com",
            "Status": "ISSUED",
            "NotAfter": not_after,
        }
    }

    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client", return_value=mock_acm):
            result = q.astrolift_provider_certs(_info(), plugin_slug="aws")

    assert len(result) == 1
    cert = result[0]
    assert cert.arn == "arn:aws:acm:us-west-2:123:certificate/abc"
    assert cert.domain == "*.example.com"
    assert cert.status == "ISSUED"
    assert "2027" in cert.not_after


def test_provider_certs_skips_describe_failure(permission_resolver, plugin, plugin_config, org):
    """describe_certificate failure on one cert skips it, doesn't abort."""
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    mock_acm = MagicMock()
    mock_page = {
        "CertificateSummaryList": [
            {"CertificateArn": "arn:aws:acm:us-west-2:123:certificate/good"},
            {"CertificateArn": "arn:aws:acm:us-west-2:123:certificate/bad"},
        ]
    }
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [mock_page]
    mock_acm.get_paginator.return_value = mock_paginator

    def _describe(CertificateArn):  # noqa: N803
        if "bad" in CertificateArn:
            raise Exception("not found")
        return {
            "Certificate": {
                "CertificateArn": CertificateArn,
                "DomainName": "good.example.com",
                "Status": "ISSUED",
                "NotAfter": None,
            }
        }

    mock_acm.describe_certificate.side_effect = _describe

    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client", return_value=mock_acm):
            result = q.astrolift_provider_certs(_info(), plugin_slug="aws")

    assert len(result) == 1
    assert result[0].domain == "good.example.com"


# ---- provider_cognito_pools (#859) -----------------------------------


def test_provider_cognito_pools_non_aws_returns_empty(permission_resolver):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    result = q.astrolift_provider_cognito_pools(_info(), plugin_slug="azure")
    assert result == []


def test_provider_cognito_pools_error_returns_empty(permission_resolver, plugin, plugin_config, org):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client") as mock_boto:
            mock_boto.return_value.list_user_pools.side_effect = Exception("no perms")
            result = q.astrolift_provider_cognito_pools(_info(), plugin_slug="aws")
    assert result == []


def test_provider_cognito_pools_happy_path(permission_resolver, plugin, plugin_config, org):
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    mock_idp = MagicMock()
    mock_idp.list_user_pools.return_value = {
        "UserPools": [{"Id": "us-west-2_ABC123", "Name": "my-pool"}],
        # No NextToken — single page.
    }
    mock_idp.describe_user_pool.return_value = {
        "UserPool": {
            "Id": "us-west-2_ABC123",
            "Arn": "arn:aws:cognito-idp:us-west-2:123:userpool/us-west-2_ABC123",
            "Name": "my-pool",
            "Domain": "my-domain",
        }
    }

    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client", return_value=mock_idp):
            result = q.astrolift_provider_cognito_pools(_info(), plugin_slug="aws")

    assert len(result) == 1
    pool = result[0]
    assert pool.pool_id == "us-west-2_ABC123"
    assert "us-west-2_ABC123" in pool.pool_arn
    assert pool.name == "my-pool"
    assert pool.domain == "my-domain"


def test_provider_cognito_pools_describe_failure_still_surfaces_pool(
    permission_resolver, plugin, plugin_config, org
):
    """If describe_user_pool fails, surface what list gave us (no ARN, no domain)."""
    q = _granted_query(permission_resolver, Permission.CLUSTER_REGISTER)
    mock_idp = MagicMock()
    mock_idp.list_user_pools.return_value = {
        "UserPools": [{"Id": "us-east-1_XYZ", "Name": "fallback-pool"}],
    }
    mock_idp.describe_user_pool.side_effect = Exception("insufficient scope")

    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client", return_value=mock_idp):
            result = q.astrolift_provider_cognito_pools(_info(), plugin_slug="aws")

    assert len(result) == 1
    pool = result[0]
    assert pool.pool_id == "us-east-1_XYZ"
    assert pool.pool_arn == ""
    assert pool.name == "fallback-pool"


# ---- provider_hosted_zones (#861) ------------------------------------


def test_provider_hosted_zones_non_aws_returns_empty(permission_resolver):
    q = _granted_query(permission_resolver, Permission.PROVIDER_PLUGIN_READ)
    result = q.astrolift_provider_hosted_zones(_info(), plugin_slug="azure")
    assert result == []


def test_provider_hosted_zones_error_returns_empty(permission_resolver, plugin, plugin_config, org):
    q = _granted_query(permission_resolver, Permission.PROVIDER_PLUGIN_READ)
    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client") as mock_boto:
            mock_boto.return_value.get_paginator.side_effect = Exception("r53 unreachable")
            result = q.astrolift_provider_hosted_zones(_info(), plugin_slug="aws")
    assert result == []


def test_provider_hosted_zones_happy_path(permission_resolver, plugin, plugin_config, org):
    q = _granted_query(permission_resolver, Permission.PROVIDER_PLUGIN_READ)
    mock_r53 = MagicMock()
    mock_page = {
        "HostedZones": [
            {"Id": "/hostedzone/Z111", "Name": "example.com.", "ResourceRecordSetCount": 12},
            {"Id": "/hostedzone/Z222", "Name": "staging.example.com.", "ResourceRecordSetCount": 3},
        ]
    }
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [mock_page]
    mock_r53.get_paginator.return_value = mock_paginator

    with tenant_context(TenantContext(organization_id=org.pk)):
        with patch("boto3.client", return_value=mock_r53):
            result = q.astrolift_provider_hosted_zones(_info(), plugin_slug="aws")

    assert len(result) == 2
    # Sorted by Name.
    assert result[0].zone_name == "example.com"
    assert result[0].zone_id == "Z111"
    assert result[0].record_count == 12
    # Trailing dot stripped from zone_name.
    assert not result[0].zone_name.endswith(".")


def test_provider_hosted_zones_denied_without_provider_plugin_read(permission_resolver):
    from core.permissions import PermissionDenied

    q = ClustersQuery()
    with pytest.raises(PermissionDenied):
        q.astrolift_provider_hosted_zones(_info(), plugin_slug="aws")
