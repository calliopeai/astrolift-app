"""Connect an organization to Zentinelle and manage its cluster gateways (#1887).

``zentinelle.connect`` connects, disconnects and rotates the credentials
Zentinelle issued; ``zentinelle.gateway_manage`` registers and unregisters
clusters and turns their gateways on and off (#1888). Every mutation is
step-up gated and audited, and no payload carries a credential. The work
itself is in ``astrolift_operations.zentinelle_connect``.
"""

from __future__ import annotations

import strawberry
from django.contrib.auth import get_user_model
from django.db.models import Q
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
from astrolift_operations.schema.mutations.helpers import _caller_org_id
from astrolift_operations.schema.mutations.types import (
    ConnectZentinelleInput,
    DisconnectZentinelleInput,
    RotateZentinelleGatewayCredentialInput,
    SetZentinelleGatewayEnabledInput,
    ZentinelleClusterInput,
)
from astrolift_operations.schema.types import (
    ZentinelleClusterGatewayType,
    ZentinelleConnectionType,
    ZentinelleDisconnectType,
    zentinelle_cluster_gateway_to_type,
    zentinelle_connection_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

ZentinelleConnectError = zentinelle_connect.ZentinelleConnectError


def _actor():
    tenant = get_current_tenant()
    if tenant is None or tenant.actor_user_id is None:
        return None
    return get_user_model().objects.filter(pk=tenant.actor_user_id).first()


def _refusal(exc: ZentinelleConnectError) -> MutationResultType[None]:
    return gql_failure(exc.code.value, exc.message, field=exc.field)


def _connection(org_id: int | None) -> ZentinelleConnection | None:
    if org_id is None:
        return None
    return ZentinelleConnection.objects.filter(organization_id=org_id).first()


def _visible_cluster(org_id: int | None, cluster_id):
    """A cluster the caller's org may act on: its own or a shared one."""
    from astrolift_clusters.models import TenantCluster

    if org_id is None:
        return None
    return (
        TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
            guid=str(cluster_id),
            deleted_at__isnull=True,
        )
        .select_related("provider_plugin")
        .first()
    )


def _gateway(org_id: int | None, cluster_id) -> ZentinelleClusterGateway | None:
    """The caller org's registration of ``cluster_id``. Another org's reads as none."""
    if org_id is None:
        return None
    return (
        ZentinelleClusterGateway.objects.filter(
            connection__organization_id=org_id,
            connection__deleted_at__isnull=True,
            cluster__guid=str(cluster_id),
        )
        .select_related("connection", "cluster", "cluster__provider_plugin")
        .first()
    )


def _not_registered() -> MutationResultType[None]:
    return gql_failure(
        ErrorCode.NOT_FOUND.value,
        "this cluster is not registered with this organization's Zentinelle connection",
        field="clusterId",
    )


def _cluster_target(root, info, input):
    return "TenantCluster", str(input.cluster_id)


def _connection_target(root, info, input):
    connection = _connection(_caller_org_id())
    return ("ZentinelleConnection", str(connection.guid)) if connection is not None else None


def _connect_extras(result):
    if not result.ok or result.data is None:
        return None
    return {
        "zentinelle_url": result.data.base_url,
        "zentinelle_install_id": result.data.zentinelle_install_id,
    }


def _disconnect_extras(result):
    if not result.ok or result.data is None:
        return None
    return {"warnings": list(result.data.warnings)}


def _gateway_extras(result):
    if not result.ok or result.data is None:
        return None
    return {"gateway_enabled": result.data.gateway_enabled, "gateway_deployed": result.data.gateway_deployed}


@strawberry.type
class ZentinelleMutations:
    @strawberry.field
    @mutation_audit(action="zentinelle.connect", extras=_connect_extras)
    @requires_elevation(action_label="Connect to Zentinelle")
    @require_permission(Permission.ZENTINELLE_CONNECT)
    @tenant_scoped()
    def connect_zentinelle(
        self, info: Info, input: ConnectZentinelleInput
    ) -> MutationResultType[ZentinelleConnectionType]:
        """Exchange a one-time Zentinelle enrollment code for this organization's
        install credential. The credential is stored encrypted and never returned."""
        from astrolift_identity.models import Organization

        org_id = _caller_org_id()
        organization = Organization.objects.filter(pk=org_id).first() if org_id is not None else None
        if organization is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "no organization in context", field="organizationId"
            )
        try:
            connection = zentinelle_connect.connect(
                organization=organization,
                url=input.url,
                code=input.enrollment_code,
                install_url=zentinelle_connect.install_base_url(),
                actor=_actor(),
            )
        except ZentinelleConnectError as exc:
            return _refusal(exc)
        return gql_success(zentinelle_connection_to_type(connection))

    @strawberry.field
    @mutation_audit(action="zentinelle.disconnect", target=_connection_target, extras=_disconnect_extras)
    @requires_elevation(action_label="Disconnect from Zentinelle")
    @require_permission(Permission.ZENTINELLE_CONNECT)
    @tenant_scoped()
    def disconnect_zentinelle(
        self, info: Info, input: DisconnectZentinelleInput
    ) -> MutationResultType[ZentinelleDisconnectType]:
        """Revoke this organization's install in Zentinelle, with every cluster it
        registered, then remove the gateways and their Secrets from the clusters."""
        connection = _connection(_caller_org_id())
        if connection is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "this organization is not connected to Zentinelle")
        try:
            outcome = zentinelle_connect.disconnect(connection=connection, actor=_actor(), force=input.force)
        except ZentinelleConnectError as exc:
            return _refusal(exc)
        return gql_success(
            ZentinelleDisconnectType(
                connection=zentinelle_connection_to_type(outcome.connection),
                warnings=outcome.warnings,
            )
        )

    @strawberry.field
    @mutation_audit(action="zentinelle.cluster.register", target=_cluster_target, extras=_gateway_extras)
    @requires_elevation(action_label="Register a cluster with Zentinelle")
    @require_permission(Permission.ZENTINELLE_GATEWAY_MANAGE)
    @tenant_scoped()
    def register_zentinelle_cluster(
        self, info: Info, input: ZentinelleClusterInput
    ) -> MutationResultType[ZentinelleClusterGatewayType]:
        """Register a cluster with this organization's Zentinelle and write its gateway
        credential into the cluster. Registering again replaces the credential. The
        gateway is deployed when it is on for the cluster and for the install."""
        org_id = _caller_org_id()
        connection = _connection(org_id)
        if connection is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "connect this organization to Zentinelle first")
        cluster = _visible_cluster(org_id, input.cluster_id)
        if cluster is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, f"cluster {input.cluster_id!r} not found", field="clusterId"
            )
        try:
            gateway = zentinelle_connect.register_cluster(connection=connection, cluster=cluster)
        except ZentinelleConnectError as exc:
            return _refusal(exc)
        return gql_success(zentinelle_cluster_gateway_to_type(gateway))

    @strawberry.field
    @mutation_audit(action="zentinelle.gateway.set_enabled", target=_cluster_target, extras=_gateway_extras)
    @requires_elevation(action_label="Turn the Zentinelle gateway on or off")
    @require_permission(Permission.ZENTINELLE_GATEWAY_MANAGE)
    @tenant_scoped()
    def set_zentinelle_gateway_enabled(
        self, info: Info, input: SetZentinelleGatewayEnabledInput
    ) -> MutationResultType[ZentinelleClusterGatewayType]:
        """Deploy a registered cluster's gateway, or remove it. Removing keeps the
        registration and the credential Secret, so turning it back on is immediate."""
        gateway = _gateway(_caller_org_id(), input.cluster_id)
        if gateway is None:
            return _not_registered()
        try:
            gateway = zentinelle_connect.set_gateway_enabled(gateway=gateway, enabled=input.enabled)
        except ZentinelleConnectError as exc:
            return _refusal(exc)
        return gql_success(zentinelle_cluster_gateway_to_type(gateway))

    @strawberry.field
    @mutation_audit(action="zentinelle.gateway.rotate_credential", target=_cluster_target)
    @requires_elevation(action_label="Rotate the Zentinelle gateway credential")
    @require_permission(Permission.ZENTINELLE_CONNECT)
    @tenant_scoped()
    def rotate_zentinelle_gateway_credential(
        self, info: Info, input: RotateZentinelleGatewayCredentialInput
    ) -> MutationResultType[ZentinelleClusterGatewayType]:
        """Mint a cluster gateway's next credential and write it into the cluster."""
        gateway = _gateway(_caller_org_id(), input.cluster_id)
        if gateway is None:
            return _not_registered()
        overlap = (
            input.overlap_seconds
            if input.overlap_seconds is not None
            else zentinelle_connect.DEFAULT_ROTATION_OVERLAP_SECONDS
        )
        try:
            gateway = zentinelle_connect.rotate_cluster(gateway=gateway, overlap_seconds=overlap)
        except ZentinelleConnectError as exc:
            return _refusal(exc)
        return gql_success(zentinelle_cluster_gateway_to_type(gateway))

    @strawberry.field
    @mutation_audit(action="zentinelle.cluster.unregister", target=_cluster_target)
    @requires_elevation(action_label="Unregister a cluster from Zentinelle")
    @require_permission(Permission.ZENTINELLE_GATEWAY_MANAGE)
    @tenant_scoped()
    def unregister_zentinelle_cluster(
        self, info: Info, input: ZentinelleClusterInput
    ) -> MutationResultType[ZentinelleClusterGatewayType]:
        """Revoke a cluster in Zentinelle, then remove its gateway and credential Secret."""
        gateway = _gateway(_caller_org_id(), input.cluster_id)
        if gateway is None:
            return _not_registered()
        try:
            gateway = zentinelle_connect.unregister_cluster(gateway=gateway, actor=_actor())
        except ZentinelleConnectError as exc:
            return _refusal(exc)
        return gql_success(zentinelle_cluster_gateway_to_type(gateway))
