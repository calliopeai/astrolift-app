"""Typed, versioned operator runtime setup; no raw provider or auth configuration."""

from datetime import datetime
from uuid import UUID

import strawberry
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID, MutationResultType, failure, success
from astrolift_graphql.results import version_mismatch
from astrolift_services.cluster_models import available_model_clusters, cluster_model_org_scope
from astrolift_services.model_admission import current_org_id, in_current_org, shared_cluster_operation
from astrolift_services.model_runtime_settings import (
    ModelRuntimeDeclaration,
    ModelRuntimeDeclarationInput,
    ModelRuntimeMode,
    declaration_config,
    declaration_to_type,
)
from astrolift_services.schema.hf_connections import require_host_admin
from astrolift_services.schema.model_mutation_audit import model_mutation_audit
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type
class ClusterModelRuntimeModeSettings:
    compute_mode: ModelRuntimeMode
    declaration: ModelRuntimeDeclaration | None
    configured: bool
    reason: str | None
    hardware_admission: str


@strawberry.type
class ClusterModelRuntimeSettings:
    organization_id: GUID
    cluster_id: GUID
    provider_id: GUID
    cluster_version: int
    provider_version: int
    modes: list[ClusterModelRuntimeModeSettings]
    observed_at: datetime


@strawberry.input
class UpdateClusterModelRuntimeInput:
    organization_id: GUID
    cluster_id: GUID
    expected_provider_id: GUID
    if_match_version: int
    expected_provider_version: int
    compute_mode: ModelRuntimeMode
    declaration: ModelRuntimeDeclarationInput


def _rows(cluster_id, provider_id):
    try:
        cluster_id, provider_id = UUID(str(cluster_id)), UUID(str(provider_id))
    except (ValueError, TypeError, AttributeError):
        return TenantCluster.objects.none()
    return TenantCluster.objects.filter(
        Q(organization_id=current_org_id()) | Q(organization_id__isnull=True),
        guid=cluster_id,
        provider_plugin__guid=provider_id,
        provider_plugin__deleted_at__isnull=True,
    )


def _snapshot(cluster):
    config = cluster.provider_config if isinstance(cluster.provider_config, dict) else {}
    runtimes = config.get("vllm_shared_runtimes", {})
    modes = []
    active = available_model_clusters(TenantCluster.objects.filter(pk=cluster.pk), current_org_id()).exists()
    for mode in ModelRuntimeMode:
        declaration = None
        reason = "Runtime serving controls are not configured for this cluster."
        raw = runtimes.get(mode.value) if isinstance(runtimes, dict) else None
        if isinstance(raw, dict):
            try:
                declaration = declaration_to_type(raw)
                check = ModelRuntimeDeclarationInput(
                    **{
                        key: getattr(declaration, key)
                        for key in (
                            "image",
                            "version",
                            "package_version",
                            "architecture",
                            "node_selector",
                            "supported_dtypes",
                            "default_dtype",
                            "default_max_model_len",
                            "max_model_len_ceiling",
                            "default_max_num_seqs",
                            "max_num_seqs_ceiling",
                            "cpu_request_ceiling",
                            "memory_request_ceiling",
                            "gpu_count_ceiling",
                            "hardware_certified",
                            "hardware_evidence",
                        )
                    },
                    hardware_attested=raw.get("hardware_certified") is True,
                )
                declaration_config(check, mode.value)
                reason = (
                    None
                    if declaration.hardware_certified
                    else "Hardware certification has not been attested."
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                declaration = None
        if not active:
            reason = "Model placement is unavailable."
        modes.append(
            ClusterModelRuntimeModeSettings(
                compute_mode=mode,
                declaration=declaration,
                configured=bool(declaration and not reason),
                reason=reason,
                hardware_admission="operator_declared",
            )
        )
    from astrolift_identity.models import Organization

    organization = Organization.objects.get(pk=current_org_id())
    return ClusterModelRuntimeSettings(
        organization_id=GUID(str(organization.guid)),
        cluster_id=GUID(str(cluster.guid)),
        provider_id=GUID(str(cluster.provider_plugin.guid)),
        cluster_version=cluster.version,
        provider_version=cluster.provider_plugin.version,
        modes=modes,
        observed_at=timezone.now(),
    )


@strawberry.type
class ModelRuntimeSettingsQuery:
    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=cluster_model_org_scope(Permission.ORG_UPDATE))
    @require_permission(Permission.CLUSTER_UPDATE, scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE))
    @tenant_scoped()
    def cluster_model_runtime_settings(
        self,
        info: Info,
        organization_id: GUID,
        cluster_id: GUID,
        expected_provider_id: GUID,
    ) -> ClusterModelRuntimeSettings | None:
        require_host_admin(info)
        if not in_current_org(organization_id):
            return None
        cluster = _rows(cluster_id, expected_provider_id).select_related("provider_plugin").first()
        if cluster is None:
            return None
        require_host_admin(info, cluster)
        return _snapshot(cluster)


@strawberry.type
class ModelRuntimeSettingsMutation:
    @strawberry.field
    @model_mutation_audit(action="model.runtime.configure")
    @require_permission(
        Permission.ORG_UPDATE,
        scope=cluster_model_org_scope(Permission.ORG_UPDATE),
        operation=shared_cluster_operation(),
    )
    @require_permission(
        Permission.CLUSTER_UPDATE,
        scope=cluster_model_org_scope(Permission.CLUSTER_UPDATE),
        operation=shared_cluster_operation(),
    )
    @tenant_scoped()
    def update_cluster_model_runtime(
        self, info: Info, input: UpdateClusterModelRuntimeInput
    ) -> MutationResultType[ClusterModelRuntimeSettings]:
        from astrolift_services.schema.cluster_model_mutations import _locked_cluster

        require_host_admin(info)
        if not in_current_org(input.organization_id):
            return failure("PRECONDITION", "Runtime target is unavailable. Review the selected cluster.")
        try:
            with transaction.atomic():
                cluster = _locked_cluster(input.cluster_id, input.expected_provider_id)
                if cluster is None:
                    return failure(
                        "PRECONDITION", "Runtime target is unavailable. Review the selected cluster."
                    )
                require_host_admin(info, cluster)
                if cluster.version != input.if_match_version:
                    return version_mismatch(
                        current_version=cluster.version,
                        requested_version=input.if_match_version,
                        kind="Cluster",
                    )
                if cluster.provider_plugin.version != input.expected_provider_version:
                    return version_mismatch(
                        current_version=cluster.provider_plugin.version,
                        requested_version=input.expected_provider_version,
                        kind="Provider",
                    )
                declaration = declaration_config(input.declaration, input.compute_mode.value)
                existing = cluster.provider_config
                if existing is not None and not isinstance(existing, dict):
                    return failure("PRECONDITION", "Stored provider configuration requires operator repair.")
                config = dict(existing or {})
                runtimes = config.get("vllm_shared_runtimes", {})
                if not isinstance(runtimes, dict):
                    return failure("PRECONDITION", "Stored runtime configuration requires operator repair.")
                require_host_admin(info, cluster)
                if declaration["hardware_certified"]:
                    declaration["hardware_attested_by_user_id"] = info.context.user.pk
                    declaration["hardware_attested_at"] = timezone.now().isoformat()
                config["vllm_shared_runtimes"] = {**runtimes, input.compute_mode.value: declaration}
                cluster.provider_config = config
                cluster.save(update_fields=["provider_config", "updated_at", "version"])
                return success(_snapshot(cluster))
        except ValueError as exc:
            return failure("VALIDATION", str(exc))
        except TypeError:
            return failure("VALIDATION", "Runtime declaration is invalid.")
