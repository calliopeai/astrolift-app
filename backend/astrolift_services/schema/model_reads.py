"""Public catalogue and authorized deployment observations (#2214)."""

from datetime import datetime
from uuid import UUID

import strawberry
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.abac import operation_attributes
from astrolift_identity.api_tokens import get_current_api_token, with_active_org_member
from astrolift_identity.models import ApiToken, Organization
from astrolift_identity.operation_context import OperationContext, environment_context
from astrolift_lifecycle.scopes import app_scope_via
from astrolift_services.cluster_models import (
    available_model_clusters,
    cluster_model_org_scope,
    live_cluster_model_by_guid,
    live_cluster_models,
)
from astrolift_services.hf_catalogue import (
    CatalogueFilters,
    HuggingFaceModelResult,
    HuggingFaceModelsPage,
    model_detail,
    search_models,
)
from astrolift_services.model_density import ClusterModelDensity, cluster_density
from astrolift_services.model_observations import (
    ModelDeploymentMetrics,
    deployment_metrics,
    observation_window,
)
from astrolift_services.model_subscription_observations import (
    ModelSubscriptionMetrics,
    subscription_metric_operation,
    subscription_metrics,
)
from astrolift_services.models import ManagedService
from astrolift_services.scopes import (
    assert_provider_cluster,
    live_managed_services,
    managed_service_scope_by_guid,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, PermissionDenied, check_permission, require_permission
from core.tenancy import get_current_tenant


def _catalogue_audience(info: Info) -> str:
    user = getattr(getattr(info.context, "request", None), "user", None)
    if (
        user is None
        or not user.is_authenticated
        or not get_user_model().objects.filter(pk=user.pk, is_active=True).exists()
    ):
        raise GraphQLError("Authentication required", extensions={"code": "UNAUTHENTICATED"})
    tenant = get_current_tenant()
    token = get_current_api_token()
    if token is not None:
        active = with_active_org_member(
            ApiToken.objects.filter(
                pk=token.pk, user_id=user.pk, is_revoked=False, deleted_at__isnull=True
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())),
            user="user",
            organization="organization",
        ).first()
        if active is None or tenant is None or active.organization_id != tenant.organization_id:
            raise GraphQLError("Authentication required", extensions={"code": "UNAUTHENTICATED"})
    return f"{user.pk}:{tenant.organization_id if tenant else ''}:{token.pk if token is not None else ''}"


def _bad_input(exc):
    return GraphQLError(str(exc), extensions={"code": "BAD_INPUT"})


def _identity(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise _bad_input(ValueError("invalid model observation identity")) from exc


def _observation_service(service_id, expected_cluster_id, expected_provider_id):
    guid, cluster_guid, provider_guid = map(
        _identity, (service_id, expected_cluster_id, expected_provider_id)
    )
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    service = live_cluster_model_by_guid(guid)
    if service is None:
        service = (
            live_managed_services(
                ManagedService.objects.filter(
                    Q(registered_app__organization_id=org_id) | Q(project__organization_id=org_id),
                    guid=guid,
                )
            )
            .select_related(
                "registered_app__organization",
                "project__organization",
                "app_environment__tenant_cluster__provider_plugin",
                "tenant_cluster__provider_plugin",
            )
            .first()
        )
    if service is None:
        return None, False
    permission = Permission.CLUSTER_REGISTER if service.organization_id else Permission.APP_READ_METRICS
    scope = (
        cluster_model_org_scope(permission)({})
        if service.organization_id
        else managed_service_scope_by_guid("service_id", permissions=(permission,))({"service_id": guid})
    )
    cluster = service.effective_cluster
    assert_provider_cluster(cluster, permission=permission)
    context = (
        environment_context(service.app_environment)
        if service.app_environment_id
        else OperationContext(
            environment=None if service.organization_id else service.effective_environment_name,
            region=cluster.region or None,
            approvals=0,
        )
    )
    with operation_attributes(**context.attributes()):
        check_permission(permission, scope=scope)
    if (
        cluster.guid != cluster_guid
        or cluster.provider_plugin.guid != provider_guid
        or cluster.provider_plugin.deleted_at is not None
    ):
        raise GraphQLError(
            "Model observation target changed or is unavailable", extensions={"code": "PRECONDITION"}
        )
    available = available_model_clusters(TenantCluster.objects.filter(pk=cluster.pk), org_id).exists()
    return service, available


@strawberry.type
class ModelReadsQuery:
    @strawberry.field
    @require_permission(
        Permission.APP_READ_METRICS,
        scope=app_scope_via(
            "astrolift_services.ManagedServiceAttachment",
            "subscription_id",
            app_path="app_environment__registered_app",
            permission=Permission.APP_READ_METRICS,
        ),
        operation=subscription_metric_operation,
    )
    @tenant_scoped()
    def astrolift_model_subscription_metrics(
        self,
        info: Info,
        organization_id: GUID,
        service_id: GUID,
        subscription_id: GUID,
        expected_cluster_id: GUID,
        expected_provider_id: GUID,
        start: datetime,
        end: datetime,
    ) -> ModelSubscriptionMetrics | None:
        from astrolift_services.model_admission import in_current_org
        from astrolift_services.models import ManagedServiceAttachment
        from astrolift_services.schema.cluster_model_mutations import _recheck_authority
        from astrolift_services.schema.cluster_models import _environment_rows

        _catalogue_audience(info)
        observation_window(start, end)
        if not in_current_org(organization_id):
            return None
        service = live_cluster_model_by_guid(_identity(service_id))
        if service is None or (service.tenant_cluster.guid, service.tenant_cluster.provider_plugin.guid) != (
            _identity(expected_cluster_id),
            _identity(expected_provider_id),
        ):
            return None
        row = (
            ManagedServiceAttachment.objects.filter(
                guid=_identity(subscription_id),
                managed_service=service,
                model_subscription=True,
                managed_service__organization_id=get_current_tenant().organization_id,
                app_environment__registered_app__organization_id=get_current_tenant().organization_id,
                app_environment__in=_environment_rows(Permission.APP_READ_METRICS),
                app_environment__tenant_cluster_id=service.tenant_cluster_id,
            )
            .select_related("app_environment__registered_app__organization")
            .first()
        )
        if row is None:
            return None
        _recheck_authority(
            info, Permission.APP_READ_METRICS, service.tenant_cluster, environment=row.app_environment
        )
        row.managed_service = service
        available = available_model_clusters(
            TenantCluster.objects.filter(
                Q(organization_id=get_current_tenant().organization_id) | Q(organization_id__isnull=True),
                pk=service.tenant_cluster_id,
            ),
            get_current_tenant().organization_id,
        ).exists()
        result = subscription_metrics(row, start, end, transport_available=available)
        current_service = live_cluster_model_by_guid(_identity(service_id))
        if current_service is None or (
            current_service.tenant_cluster.guid,
            current_service.tenant_cluster.provider_plugin.guid,
        ) != (_identity(expected_cluster_id), _identity(expected_provider_id)):
            return None
        current_row = (
            ManagedServiceAttachment.objects.filter(
                guid=_identity(subscription_id),
                managed_service=current_service,
                model_subscription=True,
                managed_service__organization_id=get_current_tenant().organization_id,
                app_environment__registered_app__organization_id=get_current_tenant().organization_id,
                app_environment__in=_environment_rows(Permission.APP_READ_METRICS),
                app_environment__tenant_cluster_id=current_service.tenant_cluster_id,
            )
            .select_related("app_environment__registered_app__organization")
            .first()
        )
        if current_row is None:
            return None
        _recheck_authority(
            info,
            Permission.APP_READ_METRICS,
            current_service.tenant_cluster,
            environment=current_row.app_environment,
        )
        return result

    @strawberry.field
    def astrolift_model_deployment_metrics(
        self,
        info: Info,
        service_id: GUID,
        expected_cluster_id: GUID,
        expected_provider_id: GUID,
        start: datetime,
        end: datetime,
    ) -> ModelDeploymentMetrics | None:
        _catalogue_audience(info)
        try:
            observation_window(start, end)
            service, available = _observation_service(service_id, expected_cluster_id, expected_provider_id)
            return (
                deployment_metrics(service, start, end, transport_available=available)
                if service is not None
                else None
            )
        except PermissionDenied as exc:
            raise GraphQLError(
                "Model observations access is denied", extensions={"code": "PERMISSION_DENIED"}
            ) from exc
        except ValueError as exc:
            raise _bad_input(exc) from exc

    @strawberry.field
    def astrolift_cluster_model_density(
        self, info: Info, cluster_id: GUID, expected_provider_id: GUID, start: datetime, end: datetime
    ) -> ClusterModelDensity | None:
        _catalogue_audience(info)
        try:
            scope = cluster_model_org_scope(Permission.CLUSTER_REGISTER)({})
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            if not Organization.objects.filter(pk=org_id, deleted_at__isnull=True).exists():
                org_id = None
            cluster = (
                TenantCluster.objects.filter(
                    Q(organization_id=org_id) | Q(organization_id__isnull=True),
                    guid=_identity(cluster_id),
                    deleted_at__isnull=True,
                    provider_plugin__deleted_at__isnull=True,
                )
                .select_related("provider_plugin")
                .first()
                if org_id
                else None
            )
            with operation_attributes(
                environment=None, region=cluster.region or None if cluster is not None else None, approvals=0
            ):
                check_permission(Permission.CLUSTER_REGISTER, scope=scope)
            observation_window(start, end)
            if cluster is None:
                return None
            if cluster.provider_plugin.guid != _identity(expected_provider_id):
                raise GraphQLError(
                    "Model observation target changed or is unavailable", extensions={"code": "PRECONDITION"}
                )
            rows = live_cluster_models(ManagedService.objects.filter(tenant_cluster=cluster), org_id)
            return cluster_density(
                cluster,
                rows,
                start,
                end,
                transport_available=available_model_clusters(
                    TenantCluster.objects.filter(pk=cluster.pk), org_id
                ).exists(),
            )
        except PermissionDenied as exc:
            raise GraphQLError(
                "Model observations access is denied", extensions={"code": "PERMISSION_DENIED"}
            ) from exc
        except ValueError as exc:
            raise _bad_input(exc) from exc

    @strawberry.field
    def astrolift_hugging_face_models(
        self,
        info: Info,
        search: str = "",
        author: str = "",
        pipeline_tag: str = "",
        library: str = "",
        license: str = "",
        gated: bool | None = None,
        sort_by: str = "downloads",
        first: int = 20,
        after: str | None = None,
    ) -> HuggingFaceModelsPage:
        audience = _catalogue_audience(info)
        try:
            return search_models(
                CatalogueFilters(
                    search=search,
                    author=author,
                    pipeline_tag=pipeline_tag,
                    library=library,
                    license=license,
                    gated=gated,
                    sort_by=sort_by,
                    first=first,
                ),
                after=after,
                audience=audience,
            )
        except ValueError as exc:
            raise _bad_input(exc) from exc

    @strawberry.field
    def astrolift_hugging_face_model(
        self, info: Info, repo_id: str, revision: str | None = None
    ) -> HuggingFaceModelResult:
        _catalogue_audience(info)
        try:
            return model_detail(repo_id, revision)
        except ValueError as exc:
            raise _bad_input(exc) from exc
