"""Operator-only native metadata and existing Bedrock connection registration."""

import strawberry
from django.db import IntegrityError, transaction
from django.db.models import F
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, failure, success
from astrolift_graphql.results import version_mismatch
from astrolift_services.cluster_models import cluster_model_org_scope
from astrolift_services.model_admission import (
    in_current_org,
    shared_cluster_operation,
    shared_model_operation,
)
from astrolift_services.model_settings import ModelSharingMode, sharing_config
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.native_model_connections import (
    NativeConnectionUnavailable,
    canonical_handle,
    catalogue,
    connection_config,
    declaration,
    enabled,
    is_bedrock_connection,
    native_identity,
    verify_source,
)
from astrolift_services.schema.bedrock_model_types import (
    BedrockModelConnectionActionType,
    BedrockModelSourceKind,
    BedrockModelSourcesType,
    BedrockModelSourceType,
    NativeModelConnectionSourceType,
    NativeModelProtocol,
)
from astrolift_services.schema.cluster_model_mutations import (
    ModelOperationUnavailable,
    _enqueue,
    _idle,
    _locked_cluster,
    _locked_model,
)
from astrolift_services.schema.hf_connections import require_host_admin
from astrolift_services.schema.model_mutation_audit import model_mutation_audit
from astrolift_services.schema.model_types import ClusterModelDeploymentType, cluster_model_to_type
from core.decorators import tenant_scoped
from core.permissions import Permission, PermissionDenied, require_permission


@strawberry.input
class BedrockModelPlacementInput:
    organization_id: GUID
    cluster_id: GUID
    expected_provider_id: GUID
    expected_cluster_version: int
    expected_provider_version: int


@strawberry.input
class BedrockModelSourceInput(BedrockModelPlacementInput):
    source_kind: BedrockModelSourceKind
    source_identifier: str


@strawberry.input
class RegisterBedrockModelConnectionInput(BedrockModelSourceInput):
    source_fingerprint: str
    name: str
    allow_subscriptions: bool
    sharing_mode: ModelSharingMode = ModelSharingMode.SHARED
    dedicated_app_id: GUID | None = None
    if_match_dedicated_app_version: int | None = None


@strawberry.input
class UpdateBedrockModelConnectionInput:
    organization_id: GUID
    id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    if_match_version: int
    name: str
    allow_subscriptions: bool
    sharing_mode: ModelSharingMode | None = None
    dedicated_app_id: GUID | None = None
    if_match_dedicated_app_version: int | None = None


@strawberry.input
class UnregisterBedrockModelConnectionInput:
    organization_id: GUID
    id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    if_match_version: int


def _cluster(info, input):
    _entry_admission(info)
    if not in_current_org(input.organization_id):
        raise NativeConnectionUnavailable("Bedrock placement is unavailable.")
    cluster = _locked_cluster(input.cluster_id, input.expected_provider_id)
    if cluster is None or (
        cluster.version != input.expected_cluster_version
        or cluster.provider_plugin.version != input.expected_provider_version
    ):
        raise NativeConnectionUnavailable("Bedrock placement changed. Refresh and retry.")
    require_host_admin(info, cluster)
    declaration(cluster)
    return cluster


def _entry_admission(info):
    from types import SimpleNamespace

    from astrolift_services.hosting_authority import current_host_operator
    from astrolift_services.schema.cluster_model_mutations import _recheck_authority

    with current_host_operator(request=getattr(info.context, "request", None)):
        _recheck_authority(info, Permission.ORG_UPDATE, SimpleNamespace(region=None))


def _reviewed_placement(cluster):
    return declaration(cluster) | {
        "cluster_version": cluster.version,
        "provider_version": cluster.provider_plugin.version,
    }


def _checkpoint(info, cluster, expected):
    cluster.refresh_from_db()
    cluster.provider_plugin.refresh_from_db()
    require_host_admin(info, cluster)
    from astrolift_clusters.models import TenantCluster
    from astrolift_services.cluster_models import available_model_clusters
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    if (
        not available_model_clusters(
            TenantCluster.objects.filter(pk=cluster.pk), tenant.organization_id if tenant else None
        ).exists()
        or _reviewed_placement(cluster) != expected
    ):
        raise NativeConnectionUnavailable("Bedrock placement or credential declaration changed.")


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 128 or any(ord(c) < 32 for c in value):
        raise NativeConnectionUnavailable("Model name must contain 1 to 128 visible characters.")
    return value.strip()


def native_source_to_type(service):
    from django.utils.dateparse import parse_datetime

    try:
        native = native_identity(service)
        state = "configured"
    except ValueError:
        return None
    return NativeModelConnectionSourceType(
        protocol=NativeModelProtocol.BEDROCK,
        source_kind=BedrockModelSourceKind(native["source_kind"]),
        account_id=native["account_id"],
        region=native["region"],
        partition=native["partition"],
        source_id=native["source_id"],
        source_arn=native["source_arn"],
        destination_model_arns=list(native["destination_model_arns"]),
        source_fingerprint=native["source_fingerprint"],
        metadata_observed_at=parse_datetime((service.config or {}).get("native_metadata_observed_at", "")),
        configuration_state=state,
    )


def _project(cluster, organization, detail):
    from types import SimpleNamespace

    from django.utils import timezone

    try:
        cfg = connection_config(cluster, organization, detail)
        registerable, reason = True, None
    except NativeConnectionUnavailable:
        from astrolift_services.native_model_connections import fingerprint

        src, identity = detail.source, detail.identity
        source = {
            "protocol": "bedrock",
            "source_kind": src.kind.value,
            "source_id": src.identifier,
            "source_arn": src.arn,
            "destination_model_arns": sorted(src.destination_model_arns),
            "account_id": identity.account_id,
            "region": identity.region,
            "partition": identity.partition,
        }
        cfg = {
            "native_connection": source
            | declaration(cluster)
            | {
                "organization_id": str(organization.guid),
                "schema": 1,
                "source_fingerprint": fingerprint(source),
            },
            "model_source": "bedrock",
            "existing_connection_only": True,
            "native_metadata_observed_at": timezone.now().isoformat(),
        }
        registerable, reason = False, "Source is not an active on-demand model or inference profile."
    fake = SimpleNamespace(
        kind="model_endpoint",
        variant="bedrock",
        organization_id=organization.pk,
        organization=organization,
        tenant_cluster=cluster,
        config=cfg,
    )
    source = detail.source
    return BedrockModelSourceType(
        identity=native_source_to_type(fake),
        name=source.name,
        provider=source.provider,
        input_modalities=list(source.input_modalities),
        output_modalities=list(source.output_modalities),
        streaming=source.streaming,
        lifecycle=source.lifecycle,
        profile_type=source.profile_type,
        registerable=registerable,
        reason=reason,
    )


_ORG = cluster_model_org_scope(Permission.ORG_UPDATE)


@strawberry.type
class BedrockModelConnectionsQuery:
    @strawberry.field
    @require_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ))
    @tenant_scoped()
    def bedrock_model_connection_support(
        self, info: Info, organization_id: GUID
    ) -> BedrockModelConnectionActionType:
        active = enabled()
        try:
            if not in_current_org(organization_id) or not active:
                raise NativeConnectionUnavailable
            _entry_admission(info)
            return BedrockModelConnectionActionType(enabled=True, allowed=True, reason=None)
        except (ValueError, PermissionDenied):
            return BedrockModelConnectionActionType(
                enabled=active,
                allowed=False,
                reason="Bedrock connections or current hosting authority are unavailable.",
            )

    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=_ORG, operation=shared_cluster_operation())
    @tenant_scoped()
    def bedrock_model_connection_action(
        self, info: Info, input: BedrockModelPlacementInput
    ) -> BedrockModelConnectionActionType:
        try:
            with transaction.atomic():
                _cluster(info, input)
            return BedrockModelConnectionActionType(enabled=True, allowed=True, reason=None)
        except (ValueError, PermissionDenied):
            return BedrockModelConnectionActionType(
                enabled=enabled(),
                allowed=False,
                reason="Bedrock connections or current hosting authority are unavailable.",
            )

    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=_ORG, operation=shared_cluster_operation())
    @tenant_scoped()
    def bedrock_model_sources(
        self,
        info: Info,
        input: BedrockModelPlacementInput,
        source_kind: BedrockModelSourceKind,
        limit: int = 100,
    ) -> BedrockModelSourcesType:
        from aws.bedrock_catalogue import BedrockCatalogueDetail, CatalogueState

        from astrolift_identity.models import Organization
        from astrolift_services.model_admission import current_org_id

        with transaction.atomic():
            cluster = _cluster(info, input)
            expected = _reviewed_placement(cluster)
            with catalogue(cluster) as reader:
                page = (
                    reader.foundation_models(limit=limit)
                    if source_kind == BedrockModelSourceKind.FOUNDATION_MODEL
                    else reader.inference_profiles(limit=limit)
                )
            _checkpoint(info, cluster, expected)
            org_id = current_org_id()
            organization = Organization.objects.get(pk=org_id)
            items = [
                _project(
                    cluster,
                    organization,
                    BedrockCatalogueDetail(CatalogueState.METADATA, page.identity, source),
                )
                for source in page.items
            ]
            return BedrockModelSourcesType(
                items=items,
                state=page.state.value,
                reason=page.reason,
                truncated=page.truncated,
                partial=page.partial,
            )

    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=_ORG, operation=shared_cluster_operation())
    @tenant_scoped()
    def bedrock_model_source(
        self, info: Info, input: BedrockModelSourceInput
    ) -> BedrockModelSourceType | None:
        from aws.bedrock_catalogue import BedrockSourceKind, CatalogueState

        from astrolift_identity.models import Organization
        from astrolift_services.model_admission import current_org_id

        with transaction.atomic():
            cluster = _cluster(info, input)
            expected = _reviewed_placement(cluster)
            with catalogue(cluster) as reader:
                detail = reader.detail(BedrockSourceKind(input.source_kind.value), input.source_identifier)
            _checkpoint(info, cluster, expected)
            if detail.state != CatalogueState.METADATA:
                return None
            org_id = current_org_id()
            return _project(cluster, Organization.objects.get(pk=org_id), detail)


@strawberry.type
class BedrockModelConnectionsMutation:
    @strawberry.field
    @model_mutation_audit(action="model.native.register")
    @require_permission(Permission.ORG_UPDATE, scope=_ORG, operation=shared_cluster_operation())
    @tenant_scoped()
    def register_bedrock_model_connection(
        self, info: Info, input: RegisterBedrockModelConnectionInput
    ) -> MutationResultType[ClusterModelDeploymentType]:
        from aws.bedrock_catalogue import BedrockSourceKind

        from astrolift_services.model_connection_policy import locked_organization

        try:
            with transaction.atomic():
                organization = locked_organization()
                cluster = _cluster(info, input)
                expected = _reviewed_placement(cluster)
                with catalogue(cluster) as reader:
                    detail = reader.detail(
                        BedrockSourceKind(input.source_kind.value), input.source_identifier
                    )
                config = connection_config(cluster, organization, detail)
                if config["native_connection"]["source_fingerprint"] != input.source_fingerprint:
                    raise NativeConnectionUnavailable(
                        "Bedrock source changed. Review the current exact source."
                    )
                service = ManagedService(
                    organization=organization,
                    tenant_cluster=cluster,
                    kind="model_endpoint",
                    variant="bedrock",
                    name=_name(input.name),
                    config=config,
                )
                config.update(sharing_config(service, input, locked=True))
                config["allow_subscriptions"] = input.allow_subscriptions
                _checkpoint(info, cluster, expected)
                service.backend_ref = canonical_handle(service)
                service.applied_config = dict(config)
                service.status = "active"
                service.save()
                return success(cluster_model_to_type(service))
        except (ValueError, PermissionDenied):
            return failure(
                "PRECONDITION",
                "Bedrock source, placement or current hosting authority is unavailable. Refresh and retry.",
            )
        except IntegrityError:
            return failure("CONFLICT", "An active model with this name already exists in this cluster.")

    @strawberry.field
    @model_mutation_audit(action="model.native.update")
    @require_permission(Permission.ORG_UPDATE, scope=_ORG, operation=shared_model_operation())
    @tenant_scoped()
    def update_bedrock_model_connection(
        self, info: Info, input: UpdateBedrockModelConnectionInput
    ) -> MutationResultType[ClusterModelDeploymentType]:
        try:
            with transaction.atomic():
                service = _locked_model(input)
                if service is None or not is_bedrock_connection(service) or not _idle(service):
                    raise NativeConnectionUnavailable
                require_host_admin(info, service.tenant_cluster)
                if service.version != input.if_match_version:
                    return version_mismatch(
                        current_version=service.version,
                        requested_version=input.if_match_version,
                        kind="Model connection",
                    )
                expected = _reviewed_placement(service.tenant_cluster)
                verify_source(service, checkpoint=lambda: _checkpoint(info, service.tenant_cluster, expected))
                sharing = sharing_config(service, input, locked=True)
                if (
                    not input.allow_subscriptions
                    and service.attachments.filter(model_subscription=True)
                    .exclude(
                        desired_enabled=False,
                        subscription_status="revoked",
                        applied_revision=F("desired_revision"),
                    )
                    .exists()
                ):
                    raise NativeConnectionUnavailable
                _checkpoint(info, service.tenant_cluster, expected)
                service.name = _name(input.name)
                service.config = dict(
                    service.config, **sharing, allow_subscriptions=input.allow_subscriptions
                )
                service.subscription_revision += 1
                service.save(
                    update_fields=["name", "config", "subscription_revision", "updated_at", "version"]
                )
                _enqueue(info, service)
                return success(cluster_model_to_type(service))
        except ModelOperationUnavailable:
            return failure("PRECONDITION", "Model connection reconciliation is unavailable. Retry later.")
        except IntegrityError:
            return failure("CONFLICT", "An active model with this name already exists in this cluster.")
        except (ValueError, PermissionDenied):
            return failure(
                "PRECONDITION", "Model connection changed or current hosting authority is unavailable."
            )

    @strawberry.field
    @model_mutation_audit(action="model.native.unregister")
    @require_permission(Permission.ORG_UPDATE, scope=_ORG, operation=shared_model_operation())
    @tenant_scoped()
    def unregister_bedrock_model_connection(
        self, info: Info, input: UnregisterBedrockModelConnectionInput
    ) -> MutationResultType[ClusterModelDeploymentType]:
        try:
            with transaction.atomic():
                service = _locked_model(input)
                if service is None or not is_bedrock_connection(service) or not _idle(service):
                    raise NativeConnectionUnavailable
                require_host_admin(info, service.tenant_cluster)
                if service.version != input.if_match_version:
                    return version_mismatch(
                        current_version=service.version,
                        requested_version=input.if_match_version,
                        kind="Model connection",
                    )
                if (
                    ManagedServiceAttachment.all_objects.filter(
                        managed_service=service, model_subscription=True
                    )
                    .exclude(
                        desired_enabled=False,
                        subscription_status="revoked",
                        applied_revision=F("desired_revision"),
                        reconciled_at__isnull=False,
                    )
                    .exists()
                ):
                    raise NativeConnectionUnavailable
                native_identity(service, cleanup=True)
                require_host_admin(info, service.tenant_cluster)
                result = cluster_model_to_type(service)
                service.soft_delete()
                return success(result)
        except (ValueError, PermissionDenied):
            return failure(
                "PRECONDITION",
                "Model still has unreconciled subscriptions or its current authority is unavailable.",
            )
