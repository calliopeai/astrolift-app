"""
Operator mutations for the cluster fleet.

Gated by ``cluster.register`` / ``cluster.update`` /
``cluster.unregister`` / ``cluster.manage`` /
``provider_plugin.configure``. The register/update/unregister
mutations are in-database registrations that the provider drivers
consume on the next reconcile / probe pass. The manage mutations
(``bringClusterIntoManagement`` / ``refreshClusterManagement``)
kick a Temporal workflow that talks to the real cluster.
"""

from __future__ import annotations

import logging

import strawberry
from django.db import transaction
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
from astrolift_workflows.client import start_workflow
from astrolift_workflows.inputs import (
    Actor,
    BringClusterIntoManagementInput,
    DecommissionClusterInput,
    InstallClusterPrereqsInput,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

logger = logging.getLogger(__name__)

JSON = strawberry.scalars.JSON


def _decommission_workflow_id(cluster_guid: str) -> str:
    return f"DecommissionClusterWorkflow-{cluster_guid}"


def _bring_workflow_id(cluster_guid: str) -> str:
    """Workflow id pattern — re-firing the same cluster joins the
    existing run rather than spawning a parallel one."""
    return f"BringClusterIntoManagement-{cluster_guid}"


def _actor_from_request(info: Info) -> Actor:
    """Same shape as ``astrolift_lifecycle.schema.mutations``. Inlined
    rather than imported to keep the lifecycle/cluster apps free of
    cross-app schema imports."""
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is not None and getattr(user, "is_authenticated", False):
        return Actor(
            kind="user",
            user_id=user.pk,
            display=getattr(user, "username", "") or "",
        )
    tenant = get_current_tenant()
    if tenant and tenant.actor_user_id:
        return Actor(kind="user", user_id=tenant.actor_user_id, display="")
    return Actor(kind="system", display="system")


def _kick_bring_into_management(
    *,
    cluster: TenantCluster,
    actor: Actor,
    force_preflight: bool,
) -> None:
    """Flip the row to ``managing`` (so the UI shows the spinner
    immediately) + enqueue the workflow.

    The workflow's first activity re-asserts ``managing`` to cover
    the worker-crash case where the row was already flipped by this
    helper; that's fine because ``mark_managing`` is idempotent.
    """
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGING.value
    cluster.last_management_error = ""
    cluster.save(
        update_fields=[
            "lifecycle",
            "last_management_error",
            "updated_at",
            "version",
        ]
    )
    start_workflow(
        "BringClusterIntoManagementWorkflow",
        args=[
            BringClusterIntoManagementInput(
                cluster_id=cluster.pk,
                actor=actor,
                force_preflight=force_preflight,
            )
        ],
        workflow_id=_bring_workflow_id(str(cluster.guid)),
    )


def _kick_decommission_cluster(
    *,
    cluster: TenantCluster,
    actor: Actor,
    delete_cloud_infra: bool = False,
) -> None:
    """Flip the row to ``decommissioning`` + enqueue the workflow.

    The workflow re-asserts the state on entry, so this synchronous
    flip is just the UI-spinner shortcut (matching the bring-into-mgmt
    pattern). The drained-check activity runs first and can flip the
    row to ``error`` if app envs are still bound — which is why this
    helper does NOT save until the workflow itself has confirmed the
    drained state. Operators see the same spinner-then-error UX that
    bring-into-management uses.

    ``delete_cloud_infra`` is the operator's opt-in for the destructive
    half of decommission — when True the workflow additionally calls
    the driver's ``teardown_cluster`` after RBAC removal.
    """
    cluster.lifecycle = TenantCluster.Lifecycle.DECOMMISSIONING.value
    cluster.last_management_error = ""
    cluster.save(
        update_fields=[
            "lifecycle",
            "last_management_error",
            "updated_at",
            "version",
        ]
    )
    start_workflow(
        "DecommissionClusterWorkflow",
        args=[
            DecommissionClusterInput(
                cluster_id=cluster.pk,
                actor=actor,
                delete_cloud_infra=delete_cloud_infra,
            ),
        ],
        workflow_id=_decommission_workflow_id(str(cluster.guid)),
    )


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


@strawberry.input
class BringClusterIntoManagementInputType:
    cluster_id: GUID


@strawberry.input
class DecommissionClusterInputType:
    cluster_id: GUID
    # Explicit opt-in for the destructive half of decommission. False
    # (default) only lifts the platform RBAC and leaves the underlying
    # EKS/GKE/AKS cluster running. True ALSO calls the driver's
    # teardown_cluster to delete the cloud-managed cluster.
    # UI surfaces this as a separate "danger zone" checkbox.
    delete_cloud_infra: bool = False


@strawberry.input
class RefreshClusterManagementInputType:
    cluster_id: GUID
    force_preflight: bool = False


@strawberry.input
class BootstrapOptionOverride:
    """One operator-set option value to flow into the install workflow."""

    component_key: str
    option_key: str
    value: str


@strawberry.input
class InstallClusterPrereqsInputType:
    """``installClusterPrereqs`` mutation input (#66).

    The operator submits the cluster + which components they chose
    + the per-option overrides as a flat triple list (component_key,
    option_key, value). The mutation re-shapes the triples into the
    nested dict the workflow consumes.
    """

    cluster_id: GUID
    selected_components: list[str]
    option_overrides: list[BootstrapOptionOverride] = strawberry.field(
        default_factory=list,
    )


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
    @mutation_audit(action="cluster.bring_into_management")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def bring_cluster_into_management(
        self, info: Info, input: BringClusterIntoManagementInputType
    ) -> MutationResultType[TenantClusterType]:
        """Operator-driven transition from ``registered`` to ``managed``
        (or ``error`` on failure). Returns the row in ``managing`` state
        so the UI can poll for completion. Idempotent — re-running
        against a managing/managed row no-ops the lifecycle flip and
        joins the in-flight workflow."""
        cluster = TenantCluster.objects.filter(guid=str(input.cluster_id), deleted_at__isnull=True).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        if not cluster.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cluster is inactive — re-activate before bringing into management",
            )
        if cluster.lifecycle == TenantCluster.Lifecycle.MANAGING.value:
            # Already in flight — surface the current state without
            # re-kicking the workflow (the existing run picks up the
            # same workflow id anyway, but skipping the DB write
            # keeps the row's updated_at stable for the UI).
            return gql_success(cluster_to_type(cluster))

        actor = _actor_from_request(info)
        with transaction.atomic():
            _kick_bring_into_management(
                cluster=cluster,
                actor=actor,
                force_preflight=True,
            )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.refresh_management")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def refresh_cluster_management(
        self, info: Info, input: RefreshClusterManagementInputType
    ) -> MutationResultType[TenantClusterType]:
        """Same workflow as ``bringClusterIntoManagement`` but accepts
        already-managed rows — the operator hits this when prereqs
        change out-of-band (cert-manager upgraded, ingress controller
        swapped) and wants the capabilities snapshot refreshed.

        ``forcePreflight=true`` re-runs the Job; default false skips
        it for a fast probe + RBAC reconcile."""
        cluster = TenantCluster.objects.filter(guid=str(input.cluster_id), deleted_at__isnull=True).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        if not cluster.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cluster is inactive — re-activate before refreshing management",
            )

        actor = _actor_from_request(info)
        with transaction.atomic():
            _kick_bring_into_management(
                cluster=cluster,
                actor=actor,
                force_preflight=bool(input.force_preflight),
            )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.decommission")
    @require_permission(Permission.CLUSTER_UNREGISTER)
    @tenant_scoped()
    def decommission_cluster(
        self, info: Info, input: DecommissionClusterInputType
    ) -> MutationResultType[TenantClusterType]:
        """Lift the platform RBAC and flip the cluster to ``decommissioned``.

        Refuses when active app environments are still bound — the
        workflow's drained-check activity surfaces the bound count in
        ``last_management_error`` and flips the row to ``error`` so the
        operator can address it. Migrate or delete bound environments
        first (see ``MigrateAppWorkflow``).

        Once decommissioned, the cluster is excluded from the
        active-cluster picker. Re-onboarding goes through
        ``bringClusterIntoManagement`` against a fresh cluster row;
        decommissioned rows are kept for audit only.
        """
        cluster = TenantCluster.objects.filter(guid=str(input.cluster_id), deleted_at__isnull=True).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        if cluster.lifecycle == TenantCluster.Lifecycle.DECOMMISSIONED.value:
            # Already terminal — surface the row without re-firing.
            return gql_success(cluster_to_type(cluster))
        if cluster.lifecycle == TenantCluster.Lifecycle.DECOMMISSIONING.value:
            # Already in flight — join the existing run.
            return gql_success(cluster_to_type(cluster))
        if cluster.lifecycle not in (
            TenantCluster.Lifecycle.MANAGED.value,
            TenantCluster.Lifecycle.ERROR.value,
        ):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"cluster lifecycle is {cluster.lifecycle!r}; only managed or error clusters can be decommissioned",
            )

        actor = _actor_from_request(info)
        with transaction.atomic():
            _kick_decommission_cluster(
                cluster=cluster,
                actor=actor,
                delete_cloud_infra=bool(input.delete_cloud_infra),
            )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.install_prereqs")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def install_cluster_prereqs(
        self, info: Info, input: InstallClusterPrereqsInputType
    ) -> MutationResultType[TenantClusterType]:
        """Apply the operator's bootstrap-recipe selection to the
        cluster (#66). Fires ``InstallClusterPrereqsWorkflow``.

        The mutation collects the operator's chosen components + per-
        option overrides; the workflow renders one Flux ``HelmRelease``
        per chosen component into ``astrolift-system`` and the
        cluster's Flux controller reconciles. Idempotent — re-running
        with a different selection converges the in-cluster state.

        Returns the cluster row immediately; the in-flight workflow
        state and individual HelmRelease ``status`` subresources flow
        into the cluster-status tab.
        """
        cluster = TenantCluster.objects.filter(
            guid=str(input.cluster_id), deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "cluster not found",
                field="clusterId",
            )
        if not cluster.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cluster is inactive — re-activate before installing prereqs",
            )

        # Reshape the flat triple list into the nested dict the
        # workflow consumes.
        overrides: dict[str, dict[str, str]] = {}
        for o in input.option_overrides or []:
            overrides.setdefault(o.component_key, {})[o.option_key] = o.value

        actor = _actor_from_request(info)
        start_workflow(
            "InstallClusterPrereqsWorkflow",
            args=[
                InstallClusterPrereqsInput(
                    cluster_id=cluster.pk,
                    actor=actor,
                    selected_components=tuple(input.selected_components),
                    option_overrides=overrides,
                ),
            ],
            workflow_id=f"InstallClusterPrereqsWorkflow-{cluster.guid}",
        )
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
