"""Read-only dependency snapshots confined to one canonical live app environment."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

import strawberry
from django.db.models import Q
from django.utils import timezone

from astrolift_clusters.heartbeat_status import resolve
from astrolift_clusters.models import ManagedDomain
from astrolift_clusters.scopes import cluster_catalog_org_scope
from astrolift_graphql import GUID
from astrolift_identity.abac import operation_attributes
from astrolift_identity.operation_context import UNKNOWN, environment_context
from astrolift_lifecycle.models import AppEnvironment, CustomDomain
from astrolift_lifecycle.visibility import live_lifecycle_rows
from astrolift_registry.scopes import _credential_scope
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import get_current_tenant

DOMAIN_LIMIT = 100


@strawberry.enum
class DependencyObservationState(Enum):
    AVAILABLE = "AVAILABLE"
    NO_DATA = "NO_DATA"
    UNAVAILABLE = "UNAVAILABLE"


@strawberry.type
class AppDependencyPermissions:
    """Effective gates at read time; elevation and mutation-specific checks still apply."""

    required_permission: str
    app_read_allowed: bool
    app_deploy_gate_allowed: bool
    cluster_register_gate_allowed: bool


@strawberry.type
class AppDependencyCluster:
    id: GUID
    slug: str
    provider_id: GUID
    provider_slug: str
    region: str | None
    lifecycle: str
    is_active: bool
    heartbeat_source: str
    heartbeat_state: DependencyObservationState
    heartbeat_status: str
    heartbeat_observed_at: datetime | None


@strawberry.type
class AppDependencyManagedDomain:
    id: GUID
    zone: str


@strawberry.type
class AppDependencyDomain:
    """App-wide persisted metadata, with no implied environment binding or live TLS proof."""

    id: GUID
    hostname: str
    is_active: bool
    validation_status: str
    validation_observed_at: datetime | None
    configured_certificate_reference_present: bool
    certificate_source: str
    stored_certificate_state: str
    certificate_metadata_state: DependencyObservationState
    certificate_observed_at: datetime | None
    stored_certificate_expires_at: datetime | None
    stored_certificate_renewal_status: str | None


@strawberry.type
class AppDependencyContext:
    app_id: GUID
    environment_id: GUID
    environment_name: str
    read_at: datetime
    permissions: AppDependencyPermissions
    cluster: AppDependencyCluster
    managed_domain: AppDependencyManagedDomain | None
    managed_domain_state: DependencyObservationState
    domains_scope: str
    domains: list[AppDependencyDomain]
    domains_state: DependencyObservationState
    domains_truncated: bool
    domain_limit: int
    live_provider_observation_state: DependencyObservationState
    live_provider_observation_reason: str


def dependency_environment(args):
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    return (
        live_lifecycle_rows(AppEnvironment.objects.all())
        .filter(
            guid=args.get("environment_id"),
            registered_app__guid=args.get("app_id"),
            registered_app__organization_id=org_id,
            tenant_cluster__provider_plugin__deleted_at__isnull=True,
            tenant_cluster__provider_plugin__is_enabled=True,
        )
        .select_related("registered_app", "tenant_cluster__provider_plugin")
        .first()
    )


def dependency_operations(args):
    env = dependency_environment(args)
    return (environment_context(env),) if env is not None else UNKNOWN


def _gate(permission, scope, environment):
    try:
        with operation_attributes(**environment_context(environment).attributes()):
            if scope.kind == ScopeKind.APP:
                scope = _credential_scope(scope, permission)
            check_permission(permission, scope=scope)
    except PermissionDenied:
        return False
    return True


def read_dependency_context(app_id, environment_id, expected_cluster_id, expected_provider_id):
    env = dependency_environment({"app_id": app_id, "environment_id": environment_id})
    if env is None:
        return None
    cluster = env.tenant_cluster
    if expected_cluster_id is not None and str(cluster.guid) != str(expected_cluster_id):
        return None
    if expected_provider_id is not None and str(cluster.provider_plugin.guid) != str(expected_provider_id):
        return None
    app_scope = PermissionScope(ScopeKind.APP, env.registered_app_id)
    with operation_attributes(**environment_context(env).attributes()):
        check_permission(Permission.APP_READ, scope=_credential_scope(app_scope, Permission.APP_READ))
    try:
        org_scope = cluster_catalog_org_scope(Permission.CLUSTER_REGISTER)({})
        cluster_gate = _gate(Permission.CLUSTER_REGISTER, org_scope, env)
    except PermissionDenied:
        cluster_gate = False
    org_id = get_current_tenant().organization_id
    domain = (
        ManagedDomain.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True), pk=env.managed_domain_id
        ).first()
        if env.managed_domain_id is not None
        else None
    )
    rows = list(
        CustomDomain.objects.filter(registered_app_id=env.registered_app_id).order_by("guid")[
            : DOMAIN_LIMIT + 1
        ]
    )
    now = timezone.now()
    return AppDependencyContext(
        app_id=GUID(str(env.registered_app.guid)),
        environment_id=GUID(str(env.guid)),
        environment_name=env.name,
        read_at=now,
        permissions=AppDependencyPermissions(
            required_permission=Permission.APP_READ.value,
            app_read_allowed=True,
            app_deploy_gate_allowed=_gate(Permission.APP_DEPLOY, app_scope, env),
            cluster_register_gate_allowed=cluster_gate,
        ),
        cluster=AppDependencyCluster(
            id=GUID(str(cluster.guid)),
            slug=cluster.slug,
            provider_id=GUID(str(cluster.provider_plugin.guid)),
            provider_slug=cluster.provider_plugin.slug,
            region=cluster.region or None,
            lifecycle=cluster.lifecycle,
            is_active=cluster.is_active,
            heartbeat_source="persisted_cluster_heartbeat",
            heartbeat_state=(
                DependencyObservationState.AVAILABLE
                if cluster.last_heartbeat_at
                else DependencyObservationState.NO_DATA
            ),
            heartbeat_status=resolve(
                last_heartbeat_at=cluster.last_heartbeat_at,
                interval_seconds=cluster.heartbeat_interval_seconds,
                now=now,
            ).value,
            heartbeat_observed_at=cluster.last_heartbeat_at,
        ),
        managed_domain=AppDependencyManagedDomain(id=GUID(str(domain.guid)), zone=domain.zone)
        if domain
        else None,
        managed_domain_state=(
            DependencyObservationState.AVAILABLE
            if domain
            else DependencyObservationState.UNAVAILABLE
            if env.managed_domain_id
            else DependencyObservationState.NO_DATA
        ),
        domains_scope="app_wide_persisted_metadata",
        domains=[
            AppDependencyDomain(
                id=GUID(str(row.guid)),
                hostname=row.hostname,
                is_active=row.is_active,
                validation_status=row.validation_status,
                validation_observed_at=row.last_checked_at,
                configured_certificate_reference_present=bool(row.sni_cert_ref or row.certificate_id),
                certificate_source="persisted_custom_domain",
                stored_certificate_state=row.certificate_state,
                certificate_metadata_state=(
                    DependencyObservationState.AVAILABLE
                    if row.cert_metadata_refreshed_at
                    else DependencyObservationState.NO_DATA
                ),
                certificate_observed_at=row.cert_metadata_refreshed_at,
                stored_certificate_expires_at=row.cert_expires_at if row.cert_metadata_refreshed_at else None,
                stored_certificate_renewal_status=(row.cert_observability_status or None)
                if row.cert_metadata_refreshed_at
                else None,
            )
            for row in rows[:DOMAIN_LIMIT]
        ],
        domains_state=DependencyObservationState.AVAILABLE if rows else DependencyObservationState.NO_DATA,
        domains_truncated=len(rows) > DOMAIN_LIMIT,
        domain_limit=DOMAIN_LIMIT,
        live_provider_observation_state=DependencyObservationState.UNAVAILABLE,
        live_provider_observation_reason="scoped_live_provider_observations_not_supported",
    )
