"""Tests for the certificate + DNS hosted-zone pickers (#858 / #861).

Two operator surfaces stop asking for hand-typed cloud identifiers:

* ``astroliftClusterCertificates(clusterId)`` — the SNI / custom-domain
  cert picker on the app Domains page. Lists the cluster provider's TLS
  certs (AWS → ACM via the EKS driver) so the operator selects instead
  of pasting an ARN.
* ``astroliftDnsZones(dnsDriver)`` + ``astroliftDnsCertificates(dnsDriver)``
  — the zone + cert pickers on the "Add managed domain" dialog. Keyed by
  the DNS-driver slug (no cluster context yet at dialog time); route53
  lists hosted zones / ACM certs through the platform's ambient creds.

Boundaries covered per resolver:

* permission denied without the gating permission
* missing / soft-deleted cluster -> ``supported=False`` (cert picker)
* provider without the capability wired -> ``supported=False``
  (the UI falls back to free-text entry)
* happy path -> rows mapped 1:1 with ``supported=True``
* supported-but-unreachable (cloud call blows up) -> ``supported=True``
  with an empty list, so the picker shows an empty state rather than
  silently reverting to manual entry

The dispatch + driver-resolution helpers in ``core.dns_discovery`` are
unit-tested directly (no Django) for the slug -> driver mapping.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _operator_info():
    """The DNS pickers list the platform account, so they are the operator's (#1932)."""
    from django.contrib.auth import get_user_model

    root = get_user_model().objects.create(username=f"root-{uuid.uuid4().hex[:6]}", is_superuser=True)
    return SimpleNamespace(context=SimpleNamespace(user=root, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
    )


@pytest.fixture
def plugin():
    # Skip BaseCoreModel.save (numeric version-increment vs CharField)
    # the same way the other cluster test suites do.
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="local",
                slug=f"local-{uuid.uuid4().hex[:6]}",
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
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


# ─── astroliftClusterCertificates (#858) ─────────────────────────────


def test_cluster_certificates_denied_without_app_deploy(cluster, org, permission_resolver):
    """The cert picker is gated on ``app.deploy`` — the same permission
    the Domains page's mutations use. No grant -> denied before any
    dispatch fires."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cluster_certificates(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
            )


def test_cluster_certificates_missing_cluster_unsupported(org, permission_resolver):
    """A non-existent cluster guid -> ``supported=False`` (empty list),
    not a raise. The form falls back to the free-text ARN field."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_certificates(
            _info(),
            cluster_id=GUID(str(uuid.uuid4())),
        )
    assert result.supported is False
    assert result.certificates == []


def test_cluster_certificates_soft_deleted_unsupported(cluster, org, permission_resolver):
    """Soft-deleted clusters are excluded -> ``supported=False``."""
    cluster.soft_delete()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_certificates(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result.supported is False
    assert result.certificates == []


def test_cluster_certificates_unsupported_provider(cluster, org, permission_resolver, monkeypatch):
    """A provider driver without ``list_certificates`` (GCP / Azure /
    k8s_native today) -> ``supported=False``. We exercise this through
    the real dispatch by making driver resolution yield a driver that
    lacks the method."""
    from core import cluster_management

    # A driver object without list_certificates -> dispatch reports
    # supported=False (the hasattr gate).
    monkeypatch.setattr(
        cluster_management,
        "_driver_for_cluster",
        lambda _cluster: SimpleNamespace(),
    )
    permission_resolver.grant(Permission.APP_DEPLOY)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_certificates(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result.supported is False
    assert result.certificates == []


def test_cluster_certificates_happy_path(cluster, org, permission_resolver, monkeypatch):
    """Dispatch returns the supported payload; the resolver maps each
    cert 1:1 onto ``ClusterCertificateType`` and reports
    ``supported=True``."""
    from core import cluster_management

    def _ok(**_kwargs):
        return {
            "supported": True,
            "certificates": [
                {
                    "arn": "arn:aws:acm:us-east-1:123456789012:certificate/abc",
                    "name": "api.acme.example",
                    "domain_name": "api.acme.example",
                    "status": "ISSUED",
                },
                {
                    "arn": "arn:aws:acm:us-east-1:123456789012:certificate/def",
                    "name": "*.acme.example",
                    "domain_name": "*.acme.example",
                    "status": "ISSUED",
                },
            ],
        }

    monkeypatch.setattr(cluster_management, "cluster_certificates_dispatch", _ok)
    permission_resolver.grant(Permission.APP_DEPLOY)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_certificates(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result.supported is True
    assert [c.arn for c in result.certificates] == [
        "arn:aws:acm:us-east-1:123456789012:certificate/abc",
        "arn:aws:acm:us-east-1:123456789012:certificate/def",
    ]
    assert result.certificates[0].domain_name == "api.acme.example"
    assert result.certificates[0].status == "ISSUED"


def test_cluster_certificates_unreachable_keeps_supported(cluster, org, permission_resolver, monkeypatch):
    """A supported driver whose cloud call fails (no creds / throttled)
    raises ``ClusterManagementError`` from dispatch. The resolver keeps
    ``supported=True`` with an empty list — the capability exists, the
    data just isn't reachable, so the picker shows an empty state."""
    from core import cluster_management

    def _boom(**_kwargs):
        raise cluster_management.ClusterManagementError("list_certificates raised AccessDenied")

    monkeypatch.setattr(cluster_management, "cluster_certificates_dispatch", _boom)
    permission_resolver.grant(Permission.APP_DEPLOY)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_certificates(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result.supported is True
    assert result.certificates == []


# ─── astroliftDnsZones (#861) ────────────────────────────────────────


def test_dns_zones_denied_without_provider_plugin_read(org, permission_resolver):
    """Zone picker is gated on ``provider_plugin.read`` (the managed-
    domains admin surface). No grant -> denied."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_dns_zones(_operator_info(), dns_driver="route53")


def test_dns_zones_unsupported_driver(org, permission_resolver):
    """A driver without zone discovery wired (cloud_dns / azure_dns
    today) -> ``supported=False`` so the UI disables the picker and
    leaves the textarea editable."""
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_dns_zones(_operator_info(), dns_driver="cloud_dns")
    assert result.supported is False
    assert result.zones == []


def test_dns_zones_happy_path(org, permission_resolver):
    """An injected DNS driver returns zone dicts; the resolver maps each
    onto ``DnsZoneType`` with ``supported=True``. config_json is carried
    through verbatim for the dialog textarea auto-fill."""
    from core import dns_discovery

    config_json = json.dumps({"zone_id": "Z123ABC", "certificate_arn": ""})
    fake = SimpleNamespace(
        list_zones=lambda: [
            {"id": "Z123ABC", "name": "acme.example.", "private": False, "config_json": config_json},
        ],
    )
    dns_discovery.set_dns_driver_for_tests(fake)
    try:
        permission_resolver.grant(Permission.PROVIDER_PLUGIN_READ)
        with tenant_context(TenantContext(organization_id=org.id)):
            result = ClustersQuery().astrolift_dns_zones(_operator_info(), dns_driver="route53")
    finally:
        dns_discovery.reset_dns_driver_for_tests()

    assert result.supported is True
    assert len(result.zones) == 1
    assert result.zones[0].id == "Z123ABC"
    assert result.zones[0].name == "acme.example."
    assert result.zones[0].private is False
    assert json.loads(result.zones[0].config_json) == {"zone_id": "Z123ABC", "certificate_arn": ""}


def test_dns_zones_list_error_keeps_supported(org, permission_resolver):
    """A supported driver whose list call fails -> ``supported=True``
    with an empty list (capability exists, data unreachable)."""
    from core import dns_discovery

    def _boom():
        raise RuntimeError("throttled")

    fake = SimpleNamespace(list_zones=_boom)
    dns_discovery.set_dns_driver_for_tests(fake)
    try:
        permission_resolver.grant(Permission.PROVIDER_PLUGIN_READ)
        with tenant_context(TenantContext(organization_id=org.id)):
            result = ClustersQuery().astrolift_dns_zones(_operator_info(), dns_driver="route53")
    finally:
        dns_discovery.reset_dns_driver_for_tests()

    assert result.supported is True
    assert result.zones == []


# ─── astroliftDnsCertificates (#858, dialog cert picker) ─────────────


def test_dns_certificates_denied_without_provider_plugin_read(org, permission_resolver):
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_dns_certificates(_operator_info(), dns_driver="route53")


def test_dns_certificates_unsupported_driver(org, permission_resolver):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_dns_certificates(_operator_info(), dns_driver="azure_dns")
    assert result.supported is False
    assert result.certificates == []


def test_dns_certificates_happy_path(org, permission_resolver):
    from core import dns_discovery

    fake = SimpleNamespace(
        list_certificates=lambda: [
            {
                "arn": "arn:aws:acm:us-east-1:123456789012:certificate/abc",
                "name": "acme.example",
                "domain_name": "acme.example",
                "status": "ISSUED",
            },
        ],
    )
    dns_discovery.set_dns_driver_for_tests(fake)
    try:
        permission_resolver.grant(Permission.PROVIDER_PLUGIN_READ)
        with tenant_context(TenantContext(organization_id=org.id)):
            result = ClustersQuery().astrolift_dns_certificates(_operator_info(), dns_driver="route53")
    finally:
        dns_discovery.reset_dns_driver_for_tests()

    assert result.supported is True
    assert len(result.certificates) == 1
    assert result.certificates[0].arn == "arn:aws:acm:us-east-1:123456789012:certificate/abc"
    assert result.certificates[0].status == "ISSUED"


# ─── cluster_certificates_dispatch (#858) ────────────────────────────


def test_cluster_certificates_dispatch_maps_driver_objects(cluster, monkeypatch):
    """The dispatch resolves the driver, calls ``list_certificates``,
    and converts the ``CertificateInfo`` objects into plain dicts the
    resolver layer consumes. ``supported=True`` when the driver
    implements the method."""
    from _sdk.cluster import CertificateInfo

    from core import cluster_management

    fake_driver = SimpleNamespace(
        list_certificates=lambda _ctx: [
            CertificateInfo(arn="arn:x", name="x.example", domain_name="x.example", status="ISSUED"),
        ],
    )
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda _c: fake_driver)
    monkeypatch.setattr(cluster_management, "_context_for_cluster", lambda _c: object())

    payload = cluster_management.cluster_certificates_dispatch(cluster=cluster)

    assert payload["supported"] is True
    assert payload["certificates"] == [
        {"arn": "arn:x", "name": "x.example", "domain_name": "x.example", "status": "ISSUED"},
    ]


def test_cluster_certificates_dispatch_unsupported_when_no_method(cluster, monkeypatch):
    """A driver without ``list_certificates`` -> ``supported=False``."""
    from core import cluster_management

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda _c: SimpleNamespace())
    payload = cluster_management.cluster_certificates_dispatch(cluster=cluster)
    assert payload == {"supported": False, "certificates": []}


def test_cluster_certificates_dispatch_unsupported_when_driver_unresolvable(cluster, monkeypatch):
    """Driver-resolution failure (plugin missing / config invalid) ->
    ``supported=False`` rather than propagating the error."""
    from core import cluster_management

    def _boom(_c):
        raise cluster_management.ClusterManagementError("no plugin")

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", _boom)
    payload = cluster_management.cluster_certificates_dispatch(cluster=cluster)
    assert payload == {"supported": False, "certificates": []}


# ─── core.dns_discovery unit tests (no Django) ───────────────────────


def test_dns_discovery_unsupported_slug_returns_unsupported():
    from core.dns_discovery import dns_certificates_dispatch, dns_zones_dispatch

    assert dns_zones_dispatch(dns_driver="cloud_dns") == {"supported": False, "zones": []}
    assert dns_certificates_dispatch(dns_driver="unknown") == {"supported": False, "certificates": []}


def test_dns_discovery_route53_resolves_real_driver():
    """For the route53 slug the discovery layer builds a real
    Route53Driver (without an injected fake). We don't exercise the AWS
    call here — that's covered by the moto-backed provider tests — only
    that resolution succeeds and the dispatch reports ``supported=True``
    even when the (unconfigured) cloud call fails."""
    from core.dns_discovery import dns_zones_dispatch

    # No AWS creds in the test env -> list_zones raises internally; the
    # dispatch swallows it into supported=True + empty list.
    result = dns_zones_dispatch(dns_driver="route53")
    assert result["supported"] is True
    assert isinstance(result["zones"], list)
