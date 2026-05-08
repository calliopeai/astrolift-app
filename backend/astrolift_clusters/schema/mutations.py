"""
Operator mutations for the cluster fleet.

Gated by ``cluster.register`` / ``cluster.update`` /
``cluster.unregister`` / ``provider_plugin.configure``. None of these
talk to a real cloud — they're in-database registrations that the
provider drivers consume on the next reconcile / probe pass.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_clusters.models import (
    ManagedDomain,
    ProviderPlugin,
    ProviderPluginConfig,
    TenantCluster,
)
from astrolift_clusters.schema.types import (
    ManagedDomainType,
    TenantClusterType,
    cluster_to_type,
    domain_to_type,
)
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from core.mutations import ErrorCode, mutation_audit
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

JSON = strawberry.scalars.JSON


@strawberry.input
class RegisterTenantClusterInput:
    slug: str
    name: str
    provider_plugin_slug: str
    auth_method: str  # kubeconfig | exec_plugin | service_account_token
    region: str | None = None
    endpoint: str | None = None
    ca_cert: str | None = None
    auth_config: JSON | None = None
    provider_config: JSON | None = None
    ingress_class: str | None = None
    organization_scoped: bool = True


@strawberry.input
class UpdateTenantClusterInput:
    id: GUID
    is_active: bool | None = None
    region: str | None = None
    endpoint: str | None = None
    ingress_class: str | None = None


@strawberry.input
class UnregisterTenantClusterInput:
    id: GUID


@strawberry.input
class CreateManagedDomainInput:
    zone: str
    dns_driver: str
    default_for: str = "none"  # tenant_apps | preview_envs | both | none
    is_wildcard_managed: bool = False
    dns_config: JSON | None = None
    organization_scoped: bool = True


@strawberry.input
class UpdateManagedDomainInput:
    id: GUID
    default_for: str | None = None
    is_wildcard_managed: bool | None = None
    dns_config: JSON | None = None


@strawberry.input
class SoftDeleteManagedDomainInput:
    id: GUID


@strawberry.input
class ConfigureProviderPluginInput:
    plugin_slug: str
    config: JSON
    organization_scoped: bool = True


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.type
class _ProviderPluginConfigPayload:
    plugin_slug: str
    organization_scoped: bool


@strawberry.type
class ClustersMutation:
    @strawberry.field
    @mutation_audit(action="cluster.register")
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def register_tenant_cluster(
        self, info: Info, input: RegisterTenantClusterInput
    ) -> MutationResultType[TenantClusterType]:
        plugin = ProviderPlugin.objects.filter(slug=input.provider_plugin_slug).first()
        if plugin is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"plugin {input.provider_plugin_slug!r} not registered",
                field="providerPluginSlug",
            )
        if input.auth_method not in {"kubeconfig", "exec_plugin", "service_account_token"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "authMethod must be kubeconfig | exec_plugin | service_account_token",
                field="authMethod",
            )
        if TenantCluster.objects.filter(slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"cluster slug {input.slug!r} already registered",
                field="slug",
            )

        org = None
        if input.organization_scoped:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            org = Organization.objects.filter(pk=org_id).first() if org_id else None

        cluster = TenantCluster.objects.create(
            organization=org,
            provider_plugin=plugin,
            slug=input.slug,
            name=input.name,
            auth_method=input.auth_method,
            region=input.region or "",
            endpoint=input.endpoint or "",
            ca_cert=input.ca_cert or "",
            auth_config=input.auth_config or {},
            provider_config=input.provider_config or {},
            ingress_class=input.ingress_class or "nginx",
        )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.update")
    @require_permission(Permission.CLUSTER_UPDATE)
    @tenant_scoped()
    def update_tenant_cluster(
        self, info: Info, input: UpdateTenantClusterInput
    ) -> MutationResultType[TenantClusterType]:
        cluster = TenantCluster.objects.filter(guid=str(input.id)).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found")
        if input.is_active is not None:
            cluster.is_active = input.is_active
        if input.region is not None:
            cluster.region = input.region
        if input.endpoint is not None:
            cluster.endpoint = input.endpoint
        if input.ingress_class is not None:
            cluster.ingress_class = input.ingress_class
        cluster.save()
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.unregister")
    @require_permission(Permission.CLUSTER_UNREGISTER)
    @tenant_scoped()
    def unregister_tenant_cluster(
        self, info: Info, input: UnregisterTenantClusterInput
    ) -> MutationResultType[_SoftDeletePayload]:
        cluster = TenantCluster.objects.filter(guid=str(input.id)).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found")

        # Refuse if active apps still target this cluster.
        from astrolift_registry.models import RegisteredApp

        in_use = RegisteredApp.objects.filter(default_tenant_cluster=cluster, is_active=True).count()
        if in_use:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"{in_use} active app(s) still target this cluster; reassign first",
            )
        cluster.soft_delete()
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- ManagedDomain ----------------------------------------------

    @strawberry.field
    @mutation_audit(action="domain.create")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def create_managed_domain(
        self, info: Info, input: CreateManagedDomainInput
    ) -> MutationResultType[ManagedDomainType]:
        if ManagedDomain.objects.filter(zone=input.zone).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"zone {input.zone!r} already registered",
                field="zone",
            )
        if input.default_for not in {"tenant_apps", "preview_envs", "both", "none"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "defaultFor must be tenant_apps | preview_envs | both | none",
                field="defaultFor",
            )

        org = None
        if input.organization_scoped:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            org = Organization.objects.filter(pk=org_id).first() if org_id else None

        domain = ManagedDomain.objects.create(
            organization=org,
            zone=input.zone,
            dns_driver=input.dns_driver,
            default_for=input.default_for,
            is_wildcard_managed=input.is_wildcard_managed,
            dns_config=input.dns_config or {},
        )
        return gql_success(domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="domain.update")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def update_managed_domain(
        self, info: Info, input: UpdateManagedDomainInput
    ) -> MutationResultType[ManagedDomainType]:
        domain = ManagedDomain.objects.filter(guid=str(input.id)).first()
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")
        if input.default_for is not None:
            domain.default_for = input.default_for
        if input.is_wildcard_managed is not None:
            domain.is_wildcard_managed = input.is_wildcard_managed
        if input.dns_config is not None:
            domain.dns_config = input.dns_config
        domain.save()
        return gql_success(domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="domain.delete")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def soft_delete_managed_domain(
        self, info: Info, input: SoftDeleteManagedDomainInput
    ) -> MutationResultType[_SoftDeletePayload]:
        domain = ManagedDomain.objects.filter(guid=str(input.id)).first()
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")
        domain.soft_delete()
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- ProviderPlugin config -------------------------------------

    @strawberry.field
    @mutation_audit(action="provider_plugin.configure")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def configure_provider_plugin(
        self, info: Info, input: ConfigureProviderPluginInput
    ) -> MutationResultType[_ProviderPluginConfigPayload]:
        plugin = ProviderPlugin.objects.filter(slug=input.plugin_slug).first()
        if plugin is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"plugin {input.plugin_slug!r} not registered",
                field="pluginSlug",
            )

        # Validate against the plugin's JSON-schema if one is declared.
        schema = plugin.config_schema or {}
        if schema:
            try:
                import jsonschema

                jsonschema.validate(input.config, schema)
            except Exception as exc:  # noqa: BLE001
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"config failed schema validation: {exc}",
                    field="config",
                )

        org = None
        if input.organization_scoped:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            org = Organization.objects.filter(pk=org_id).first() if org_id else None

        ProviderPluginConfig.objects.update_or_create(
            organization=org,
            provider_plugin=plugin,
            defaults={"config": input.config},
        )
        return gql_success(
            _ProviderPluginConfigPayload(
                plugin_slug=plugin.slug,
                organization_scoped=input.organization_scoped,
            )
        )
