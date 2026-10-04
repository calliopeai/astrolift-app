"""Organization-owned model catalogue and destination-authorized subscriptions."""

from uuid import UUID

import strawberry
from django.db import transaction
from django.db.models import CharField, Exists, F, Func, OuterRef, Q
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID, PageType, numbered_page
from astrolift_identity.operation_visibility import visible_operation_rows
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.scopes import live_app_owners
from astrolift_services.cluster_models import (
    available_model_clusters,
    cluster_model_org_scope,
    live_cluster_model_by_guid,
    live_cluster_models,
)
from astrolift_services.model_admission import (
    current_org_id,
    in_current_org,
    shared_cluster_operation,
    shared_model_operation,
    validate_cluster_request,
    with_canonical_model_handle,
)
from astrolift_services.model_runtime_settings import ModelDtype
from astrolift_services.model_settings import (
    UpdateClusterModelInput,
    allows_app,
    sharing_config,
    update_placement,
    validate_updated_source,
)
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.schema.model_types import (
    ClusterModelDeploymentType,
    ModelRuntimeAdmissionType,
    ModelSubscriptionTargetType,
    ModelSubscriptionType,
    cluster_model_to_type,
    model_subscription_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, check_permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input
class ProvisionClusterModelInput:
    organization_id: GUID
    cluster_id: GUID
    expected_provider_id: GUID
    name: str
    compute_mode: str
    cpu_request: str
    memory_request: str
    gpu_count: int
    allow_subscriptions: bool
    model_repo: str | None = None
    revision_sha: str | None = None
    local_artifact_id: GUID | None = None
    expected_artifact_version: int | None = None
    cpu_kv_cache_gi_b: int | None = None
    connection_id: GUID | None = None
    expected_connection_version: int | None = None
    dtype: ModelDtype | None = None
    max_model_len: int | None = None
    max_num_seqs: int | None = None


@strawberry.input
class ClusterModelsFilterInput:
    cluster_id: GUID | None = None
    compute_mode: str | None = None
    status: str | None = None
    ready: bool | None = None
    subscriptions_enabled: bool | None = None
    deployed_by_me: bool | None = None


def _guid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _page(rows, *, page, page_size, projection):
    result = numbered_page(rows, order_by=["pk"], page=page, page_size=page_size, max_page_size=50)
    return PageType(
        items=[projection(row) for row in result.rows],
        page=result.page,
        page_size=result.page_size,
        total_count=result.total_count,
    )


def _search(rows, search, fields):
    text = (search or "").strip()[:200]
    predicate = Q()
    if text:
        for field in fields:
            predicate |= Q(**{f"{field}__icontains": text})
    return rows.filter(predicate)


def _environment_rows(permission, *, approvals=0):
    owners = live_app_owners(
        RegisteredApp.objects.filter(
            organization_id=current_org_id(),
            organization__deleted_at__isnull=True,
        )
    )
    rows = AppEnvironment.objects.filter(
        registered_app__in=owners,
        tenant_cluster__deleted_at__isnull=True,
    ).select_related("registered_app__organization", "tenant_cluster")
    if permission == Permission.APP_UPDATE:
        rows = rows.exclude(registered_app__provisioning_status__in=("tearing_down", "deregistered"))
    if approvals:
        from django.db.models import IntegerField, Value

        rows = rows.annotate(_model_connection_approvals=Value(approvals, output_field=IntegerField()))
        return visible_operation_rows(
            rows, permission, environment_path="self", approvals_field="_model_connection_approvals"
        )
    return visible_operation_rows(rows, permission, environment_path="self")


def _subscription_targets_page(service, rows, *, search, page, page_size, projection=None):
    from astrolift_services.model_subscriptions import LONG_RUNNING_KINDS

    workloads = Workload.objects.filter(registered_app_id=OuterRef("registered_app_id"))
    rows = rows.annotate(
        _has_workloads=Exists(workloads),
        _unsupported_workloads=Exists(workloads.exclude(kind__in=LONG_RUNNING_KINDS)),
    )
    if service is None:
        rows = rows.none()
    admitted = bool(
        service
        and (service.config or {}).get("allow_subscriptions") is True
        and service.status in ("active", "updating")
        and service.backend_ref
        and service.applied_config is not None
    )
    if service is not None:
        admitted = (
            admitted
            and available_model_clusters(
                TenantCluster.objects.filter(
                    Q(organization_id=current_org_id()) | Q(organization_id__isnull=True),
                    pk=service.tenant_cluster_id,
                ),
                current_org_id(),
            ).exists()
        )
        decision = cluster_model_to_type(service)
        admitted = (
            admitted
            and decision.runtime_supported is True
            and decision.ready is True
            and service.status == "active"
        )

    def project(env):
        from _sdk.k8s_naming import app_namespace

        from core.app_deploy import namespace_for_environment

        canonical_namespace = app_namespace(
            organization_slug=env.registered_app.organization.slug, app_slug=env.registered_app.slug
        )
        supported_namespace = namespace_for_environment(env) == canonical_namespace
        supported_workloads = env._has_workloads and not env._unsupported_workloads
        eligible = (
            admitted
            and service.tenant_cluster_id == env.tenant_cluster_id
            and allows_app(service, env.registered_app)
            and supported_namespace
            and supported_workloads
        )
        return ModelSubscriptionTargetType(
            environment_id=GUID(str(env.guid)),
            environment_version=env.version,
            app_id=GUID(str(env.registered_app.guid)),
            app_slug=env.registered_app.slug,
            app_name=env.registered_app.name,
            environment_name=env.name,
            cluster_id=GUID(str(env.tenant_cluster.guid)),
            eligible=eligible,
            reason=None
            if eligible
            else (
                "Custom or preview namespaces are not supported for shared model subscriptions."
                if not supported_namespace
                else (
                    "Shared model subscriptions require existing long-running Deployment or StatefulSet workloads."
                    if not supported_workloads
                    else "Environment and model placement or subscription admission is unavailable."
                )
            ),
        )

    return _page(
        _search(rows, search, ("name", "registered_app__slug", "registered_app__name")),
        page=page,
        page_size=page_size,
        projection=(lambda env: projection(env, project(env))) if projection else project,
    )


@strawberry.type(name="ModelPlacementCluster")
class ModelPlacementClusterType:
    id: GUID
    provider_id: GUID
    name: str
    slug: str
    region: str | None


@strawberry.type(name="ModelDedicatedApp")
class ModelDedicatedAppType:
    id: GUID
    version: int
    name: str
    slug: str


@strawberry.type
class ClusterModelsQuery:
    @strawberry.field
    @require_permission(
        Permission.ORG_UPDATE,
        scope=cluster_model_org_scope(Permission.ORG_UPDATE),
        operation=shared_cluster_operation("cluster_id"),
    )
    @tenant_scoped()
    def cluster_model_dedicated_apps_page(
        self,
        info: Info,
        organization_id: GUID,
        cluster_id: GUID,
        expected_provider_id: GUID,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PageType[ModelDedicatedAppType]:
        from astrolift_services.schema.hf_connections import require_host_admin

        cluster = (
            available_model_clusters(
                TenantCluster.objects.filter(
                    guid=_guid(cluster_id), provider_plugin__guid=_guid(expected_provider_id)
                ),
                current_org_id(),
            ).first()
            if in_current_org(organization_id)
            else None
        )
        rows = live_app_owners(RegisteredApp.objects.filter(organization_id=current_org_id())).exclude(
            provisioning_status__in=("tearing_down", "deregistered")
        )
        if cluster is None:
            rows = rows.none()
        else:
            require_host_admin(info, cluster)
            from astrolift_registry.visibility import visible_registry_apps

            rows = visible_registry_apps(rows, Permission.APP_READ)
            rows = rows.filter(
                environments__tenant_cluster_id=cluster.pk,
                environments__deleted_at__isnull=True,
            ).distinct()
        return _page(
            _search(rows, search, ("name", "slug")),
            page=page,
            page_size=page_size,
            projection=lambda app: ModelDedicatedAppType(
                id=GUID(str(app.guid)), version=app.version, name=app.name, slug=app.slug
            ),
        )

    @strawberry.field
    @require_permission(
        Permission.ORG_UPDATE,
        scope=cluster_model_org_scope(Permission.ORG_UPDATE),
        operation=shared_model_operation(),
    )
    @require_permission(
        Permission.CLUSTER_UPDATE,
        scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE),
        operation=shared_model_operation(),
    )
    @tenant_scoped()
    def cluster_model_update_admission(
        self, info: Info, input: UpdateClusterModelInput
    ) -> ModelRuntimeAdmissionType:
        from astrolift_services.schema.cluster_model_mutations import _locked_model
        from astrolift_services.schema.hf_connections import require_host_admin

        try:
            with transaction.atomic():
                service = _locked_model(input)
                if service is None or service.version != input.if_match_version:
                    raise ValueError("Model deployment changed or is unavailable. Refresh and retry.")
                require_host_admin(info, service.tenant_cluster)
                _, runtime = validate_updated_source(service, update_placement(service, input), locked=True)
                sharing_config(service, input, locked=True)
                require_host_admin(info, service.tenant_cluster)
        except (TypeError, ValueError) as exc:
            return ModelRuntimeAdmissionType(
                eligible=False, reason=str(exc), runtime_version=None, architecture=None
            )
        return ModelRuntimeAdmissionType(
            eligible=True,
            reason=None,
            runtime_version="0.15.1",
            architecture=runtime.architecture,
            hardware_admission="operator_declared",
        )

    @strawberry.field
    @require_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ))
    @tenant_scoped()
    def cluster_model_placement_clusters_page(
        self,
        info: Info,
        organization_id: GUID,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PageType[ModelPlacementClusterType]:
        rows = available_model_clusters(TenantCluster.objects.all(), current_org_id()).select_related(
            "provider_plugin"
        )
        if not in_current_org(organization_id):
            rows = rows.none()
        return _page(
            _search(rows, search, ("name", "slug", "region")),
            page=page,
            page_size=page_size,
            projection=lambda cluster: ModelPlacementClusterType(
                id=GUID(str(cluster.guid)),
                provider_id=GUID(str(cluster.provider_plugin.guid)),
                name=cluster.name,
                slug=cluster.slug,
                region=cluster.region or None,
            ),
        )

    @strawberry.field
    @require_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ))
    @tenant_scoped()
    def cluster_model_deployments_page(
        self,
        info: Info,
        organization_id: GUID,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
        filter: ClusterModelsFilterInput | None = None,
    ) -> PageType[ClusterModelDeploymentType]:
        rows = live_cluster_models(ManagedService.objects.all(), current_org_id()).select_related(
            "organization",
            "tenant_cluster__provider_plugin",
        )
        if not in_current_org(organization_id):
            rows = rows.none()
        if filter is not None:
            if filter.cluster_id is not None:
                rows = rows.filter(tenant_cluster__guid=_guid(filter.cluster_id))
            if filter.compute_mode is not None:
                rows = rows.filter(config__compute_mode=filter.compute_mode)
            if filter.status is not None:
                rows = rows.filter(status=filter.status)
            if filter.subscriptions_enabled is not None:
                rows = rows.filter(config__allow_subscriptions=filter.subscriptions_enabled)
            if filter.deployed_by_me is not None:
                tenant = get_current_tenant()
                mine = (
                    Q(created_by_id=tenant.actor_user_id) if tenant and tenant.actor_user_id else Q(pk__in=[])
                )
                rows = rows.filter(mine if filter.deployed_by_me else ~mine)
            if filter.ready is not None:
                rows = with_canonical_model_handle(rows).annotate(
                    _applied_type=Func(
                        F("applied_config"), function="jsonb_typeof", output_field=CharField()
                    ),
                    _replicas_type=Func(
                        F("applied_config__replicas"), function="jsonb_typeof", output_field=CharField()
                    ),
                )
                ready = Q(
                    _applied_type="object",
                    status="active",
                    applied_config__isnull=False,
                    model_ready_observed_at__isnull=False,
                    model_ready_generation__gt=0,
                    model_ready_auth_revision=F("subscription_revision"),
                    model_ready_provider_guid=F("tenant_cluster__provider_plugin__guid"),
                    model_ready_backend_ref=F("backend_ref"),
                    backend_ref=F("_canonical_model_handle"),
                    applied_subscription_revision=F("subscription_revision"),
                    tenant_cluster__is_active=True,
                    tenant_cluster__lifecycle="managed",
                    tenant_cluster__provider_plugin__is_enabled=True,
                ) & ~Q(backend_ref="")
                ready &= ~Q(applied_config__has_key="replicas") | Q(
                    _replicas_type="number", applied_config__replicas__regex=r"^[1-9][0-9]*$"
                )
                rows = rows.filter(ready if filter.ready else ~ready)
        return _page(
            _search(rows, search, ("name", "config__model")),
            page=page,
            page_size=page_size,
            projection=cluster_model_to_type,
        )

    @strawberry.field
    @require_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ))
    @tenant_scoped()
    def cluster_model_deployment(
        self, info: Info, organization_id: GUID, id: GUID
    ) -> ClusterModelDeploymentType | None:
        if not in_current_org(organization_id):
            return None
        row = live_cluster_model_by_guid(id)
        return cluster_model_to_type(row) if row else None

    @strawberry.field
    @require_permission(
        Permission.CLUSTER_UPDATE,
        scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE),
        operation=shared_cluster_operation(),
    )
    @tenant_scoped()
    def cluster_model_runtime_admission(
        self, info: Info, input: ProvisionClusterModelInput
    ) -> ModelRuntimeAdmissionType:
        if not in_current_org(input.organization_id):
            return ModelRuntimeAdmissionType(
                eligible=False,
                reason="Model placement is unavailable.",
                runtime_version=None,
                architecture=None,
            )
        cluster = available_model_clusters(
            TenantCluster.objects.filter(
                Q(organization_id=current_org_id()) | Q(organization_id__isnull=True),
                guid=_guid(input.cluster_id),
                provider_plugin__guid=_guid(input.expected_provider_id),
            ),
            current_org_id(),
        ).first()
        if cluster is None:
            return ModelRuntimeAdmissionType(
                eligible=False,
                reason="Model placement is unavailable.",
                runtime_version=None,
                architecture=None,
            )
        from astrolift_services.schema.hf_connections import require_host_admin

        require_host_admin(info, cluster)
        try:
            _, runtime = validate_cluster_request(input, cluster)
        except (TypeError, ValueError) as exc:
            return ModelRuntimeAdmissionType(
                eligible=False, reason=str(exc), runtime_version=None, architecture=None
            )
        require_host_admin(info, cluster)
        return ModelRuntimeAdmissionType(
            eligible=True,
            reason=None,
            runtime_version="0.15.1",
            architecture=runtime.architecture,
            hardware_admission="operator_declared",
        )

    @strawberry.field
    @require_permission(Permission.APP_UPDATE, any_scope=True)
    @tenant_scoped()
    def cluster_model_subscription_targets_page(
        self,
        info: Info,
        organization_id: GUID,
        model_deployment_id: GUID,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PageType[ModelSubscriptionTargetType]:
        check_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ)({}))
        service = live_cluster_model_by_guid(model_deployment_id) if in_current_org(organization_id) else None
        return _subscription_targets_page(
            service, _environment_rows(Permission.APP_UPDATE), search=search, page=page, page_size=page_size
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def cluster_model_subscriptions_page(
        self,
        info: Info,
        organization_id: GUID,
        model_deployment_id: GUID,
        app_environment_id: GUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PageType[ModelSubscriptionType]:
        check_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ)({}))
        service = live_cluster_model_by_guid(model_deployment_id) if in_current_org(organization_id) else None
        envs = _environment_rows(Permission.APP_READ)
        if app_environment_id is not None:
            envs = envs.filter(guid=_guid(app_environment_id))
        rows = ManagedServiceAttachment.objects.filter(
            model_subscription=True, managed_service=service, app_environment__in=envs
        ).select_related(
            "managed_service",
            "app_environment__registered_app",
        )
        if service is None:
            rows = rows.none()
        rows = rows.annotate(
            _can_revoke=Exists(
                _environment_rows(Permission.APP_UPDATE).filter(
                    pk=OuterRef("app_environment_id"), registered_app__organization_id=current_org_id()
                )
            )
        )
        return _page(
            _search(
                rows,
                search,
                (
                    "binding_alias",
                    "app_environment__name",
                    "app_environment__registered_app__slug",
                    "app_environment__registered_app__name",
                ),
            ),
            page=page,
            page_size=page_size,
            projection=lambda row: model_subscription_to_type(
                row, can_revoke=row._can_revoke and row.subscription_status != "revoked"
            ),
        )
