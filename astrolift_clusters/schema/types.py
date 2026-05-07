from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftTenantCluster")
class TenantClusterType:
    id: GUID
    slug: str
    name: str
    organization_slug: str | None
    provider_plugin_slug: str
    region: str
    endpoint: str
    auth_method: str
    ingress_class: str
    is_active: bool
    capabilities: JSON
    capabilities_probed_at: dt.datetime | None
    created_at: dt.datetime


@strawberry.type(name="AstroliftManagedDomain")
class ManagedDomainType:
    id: GUID
    zone: str
    organization_slug: str | None
    dns_driver: str
    default_for: str
    is_wildcard_managed: bool
    created_at: dt.datetime


@strawberry.type(name="AstroliftProviderPlugin")
class ProviderPluginType:
    id: GUID
    slug: str
    name: str
    version: str
    capabilities_manifest: JSON
    is_enabled: bool


def cluster_to_type(cluster) -> TenantClusterType:
    return TenantClusterType(
        id=GUID(str(cluster.guid)),
        slug=cluster.slug,
        name=cluster.name,
        organization_slug=cluster.organization.slug if cluster.organization_id else None,
        provider_plugin_slug=cluster.provider_plugin.slug,
        region=cluster.region or "",
        endpoint=cluster.endpoint or "",
        auth_method=cluster.auth_method,
        ingress_class=cluster.ingress_class,
        is_active=cluster.is_active,
        capabilities=cluster.capabilities or {},
        capabilities_probed_at=cluster.capabilities_probed_at,
        created_at=cluster.created_at,
    )


def domain_to_type(domain) -> ManagedDomainType:
    return ManagedDomainType(
        id=GUID(str(domain.guid)),
        zone=domain.zone,
        organization_slug=domain.organization.slug if domain.organization_id else None,
        dns_driver=domain.dns_driver,
        default_for=domain.default_for,
        is_wildcard_managed=domain.is_wildcard_managed,
        created_at=domain.created_at,
    )


def plugin_to_type(plugin) -> ProviderPluginType:
    return ProviderPluginType(
        id=GUID(str(plugin.guid)),
        slug=plugin.slug,
        name=plugin.name,
        version=plugin.version,
        capabilities_manifest=plugin.capabilities_manifest or {},
        is_enabled=plugin.is_enabled,
    )
