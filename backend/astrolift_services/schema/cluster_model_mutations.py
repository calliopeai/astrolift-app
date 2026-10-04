"""Explicit shared placement and destination-authorized named subscriptions."""

from dataclasses import dataclass, replace

import strawberry
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from strawberry.types import Info

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID, MutationResultType, failure, success
from astrolift_graphql.results import version_mismatch
from astrolift_identity.operation_context import environment_operation
from astrolift_lifecycle.scopes import app_scope_via, environment_app_scope
from astrolift_services.cluster_models import (
    available_model_clusters,
    cluster_model_org_scope,
    live_cluster_model_by_guid,
    live_cluster_models,
    model_binding_prefix,
)
from astrolift_services.model_admission import (
    current_org_id,
    in_current_org,
    shared_cluster_operation,
    shared_model_operation,
    validate_cluster_request,
)
from astrolift_services.model_settings import (
    UpdateClusterModelInput,
    allows_app,
    sharing_config,
    update_placement,
    validate_updated_source,
)
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.schema.cluster_models import ProvisionClusterModelInput, _environment_rows, _guid
from astrolift_services.schema.model_mutation_audit import model_mutation_audit
from astrolift_services.schema.model_types import (
    ClusterModelDeploymentType,
    ModelSubscriptionOperationType,
    cluster_model_to_type,
    model_subscription_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, check_permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input
class SubscribeClusterModelInput:
    organization_id: GUID
    model_deployment_id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    app_environment_id: GUID
    alias: str
    if_match_version: int
    if_match_environment_version: int


@strawberry.input
class RevokeModelSubscriptionInput:
    organization_id: GUID
    id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    if_match_version: int
    if_match_deployment_version: int


@strawberry.input
class DeprovisionClusterModelInput:
    organization_id: GUID
    id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID
    if_match_version: int
    delete_data: bool = False


class ModelOperationUnavailable(Exception):
    pass


@dataclass(frozen=True)
class _ModelIdentity:
    id: GUID
    organization_id: GUID
    expected_cluster_id: GUID
    expected_provider_id: GUID


def _locked_cluster(cluster_id, expected_provider_id):
    org_id = current_org_id()
    if org_id is None:
        return None
    cluster = (
        TenantCluster.objects.filter(Q(organization_id=org_id) | Q(organization_id__isnull=True))
        .select_for_update()
        .filter(guid=_guid(cluster_id))
        .first()
    )
    if cluster is None:
        return None
    provider = (
        ProviderPlugin.objects.filter(
            Q(clusters__organization_id=org_id) | Q(clusters__organization_id__isnull=True),
            clusters__pk=cluster.pk,
        )
        .select_for_update(of=("self",))
        .filter(pk=cluster.provider_plugin_id, guid=_guid(expected_provider_id))
        .first()
    )
    if (
        provider is None
        or not available_model_clusters(
            TenantCluster.objects.filter(pk=cluster.pk), current_org_id()
        ).exists()
    ):
        return None
    cluster.provider_plugin = provider
    return cluster


def _locked_model(input):
    service = live_cluster_model_by_guid(input.id)
    if service is None or not in_current_org(input.organization_id):
        return None
    service = (
        live_cluster_models(ManagedService.objects.filter(pk=service.pk), current_org_id())
        .select_for_update(of=("self",))
        .select_related("organization")
        .first()
    )
    if service is None:
        return None
    cluster = _locked_cluster(input.expected_cluster_id, input.expected_provider_id)
    if cluster is None or service.tenant_cluster_id != cluster.pk:
        return None
    service.tenant_cluster = cluster
    if not live_cluster_models(ManagedService.objects.filter(pk=service.pk), current_org_id()).exists():
        return None
    return service


def _recheck_authority(info, permission, cluster, *, environment=None):
    """Re-read identity/grants and evaluate the locked operation, not the pre-lock snapshot."""
    from django.db.models import Q

    from astrolift_identity import abac
    from astrolift_identity.api_tokens import (
        get_current_api_token,
        reset_current_api_token,
        session_may_act_in,
        set_current_api_token,
        with_active_org_member,
    )
    from astrolift_identity.models import ApiToken
    from astrolift_services.schema.model_reads import _catalogue_audience
    from core.permissions import PermissionDenied

    token = get_current_api_token()
    marker = None
    try:
        if token is not None:
            active = with_active_org_member(
                ApiToken.objects.filter(pk=token.pk, is_revoked=False, deleted_at__isnull=True).filter(
                    Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
                ),
                user="user",
                organization="organization",
            ).first()
            tenant = get_current_tenant()
            if (
                active is None
                or active.organization_id != current_org_id()
                or active.user_id != tenant.actor_user_id
            ):
                raise PermissionDenied(permission, None, "Authentication required")
            marker = set_current_api_token(active)
        try:
            _catalogue_audience(info)
        except Exception:
            raise PermissionDenied(permission, None, "Authentication required") from None
        tenant = get_current_tenant()
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
        if actor is None or not session_may_act_in(actor, tenant.organization_id):
            raise PermissionDenied(permission, None, "Authentication required")
        request = getattr(info.context, "request", None)
        from django.http import HttpRequest

        if token is None and (isinstance(request, HttpRequest) or hasattr(request, "session")):
            from types import SimpleNamespace

            from core.current_session import fresh_authenticated_session

            current_session = fresh_authenticated_session(
                request, actor_user_id=actor.pk, permission=permission
            )
            auth_attrs = abac.attributes_from_request(
                SimpleNamespace(META=getattr(request, "META", {}), session=current_session), actor.pk
            )
        else:
            # Bearer ceilings are refreshed above; direct non-HTTP fixtures have
            # no persisted session. Keep their existing operation facts.
            auth_attrs = abac.attributes_for(tenant.actor_user_id)
        attrs = replace(
            auth_attrs,
            cache={},
            environment=environment.name if environment is not None else None,
            region=cluster.region or None,
            approvals=0,
        )
        with abac.request_attributes(attrs):
            scope = (
                environment_app_scope("id", permission=permission)({"id": str(environment.guid)})
                if environment is not None
                else cluster_model_org_scope(permission)({})
            )
            if environment is not None:
                from astrolift_services.model_connection_policy import check_connection_permission

                check_connection_permission(permission, scope=scope)
            else:
                check_permission(permission, scope=scope)
            # A platform operator is not exempt from a connection approval/deny policy.
            if environment is not None and permission == Permission.APP_UPDATE:
                from astrolift_identity.permission_resolver import _candidate_scopes, decide

                if decide(tenant, permission, scope).superuser:
                    result = abac.evaluate(
                        abac.new_subject(
                            organization_id=tenant.organization_id,
                            actor_user_id=tenant.actor_user_id,
                            permission=permission.value,
                            chain=_candidate_scopes(tenant, scope),
                            roles_at_target=[],
                            groups=[],
                            attrs=attrs,
                        )
                    )
                    if result.denied:
                        raise PermissionDenied(
                            permission, scope, "model connection policy denies this operation"
                        )
            if environment is not None:
                check_connection_permission(
                    Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ)({})
                )
    finally:
        if marker is not None:
            reset_current_api_token(marker)


def _locked_environment(guid, *, approvals=0):
    from astrolift_registry.models import RegisteredApp

    initial = (
        _environment_rows(Permission.APP_UPDATE, approvals=approvals)
        .filter(guid=_guid(guid), registered_app__organization_id=current_org_id())
        .values_list("pk", "registered_app_id")
        .first()
    )
    if initial is None:
        return None
    if (
        RegisteredApp.objects.select_for_update()
        .filter(pk=initial[1], organization_id=current_org_id())
        .first()
        is None
    ):
        return None
    return (
        _environment_rows(Permission.APP_UPDATE, approvals=approvals)
        .select_for_update(of=("self",))
        .filter(pk=initial[0], registered_app__organization_id=current_org_id())
        .first()
    )


def _idle(service):
    return service.status in (ManagedService.Status.ACTIVE, ManagedService.Status.FAILED)


def _enqueue(info, service, *, action="apply", delete_data=False):
    from astrolift_services.native_model_connections import is_bedrock_connection
    from astrolift_services.schema.mutations.managed_services import _workflow_actor
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import SharedModelReconcileInput

    workflow_name = (
        "NativeModelConnectionReconcileWorkflow"
        if is_bedrock_connection(service)
        else "SharedModelReconcileWorkflow"
    )
    if is_bedrock_connection(service) and action != "apply":
        raise ModelOperationUnavailable
    service.operation_kind = "deprovision" if action == "delete" else "reconcile"
    service.operation_workflow_id = f"{workflow_name}-{service.guid}-{service.subscription_revision}"
    service.model_operation_cluster_guid = service.tenant_cluster.guid
    service.model_operation_provider_guid = service.tenant_cluster.provider_plugin.guid
    service.operation_started_at = timezone.now()
    service.operation_completed_at = None
    service.status = (
        ManagedService.Status.DEPROVISIONING if action == "delete" else ManagedService.Status.UPDATING
    )
    service.status_error = ""
    service.save(
        update_fields=[
            "model_operation_cluster_guid",
            "model_operation_provider_guid",
            "operation_kind",
            "operation_workflow_id",
            "operation_started_at",
            "operation_completed_at",
            "status",
            "status_error",
            "updated_at",
            "version",
        ]
    )
    try:
        handle = start_workflow(
            workflow_name,
            args=[
                SharedModelReconcileInput(
                    service.pk, service.subscription_revision, _workflow_actor(info), action, delete_data
                )
            ],
            workflow_id=service.operation_workflow_id,
        )
        if not handle.enqueued:
            raise ModelOperationUnavailable
    except Exception as exc:
        raise ModelOperationUnavailable from exc
    service.operation_run_id = str(handle.run_id or "")
    service.save(update_fields=["operation_run_id", "updated_at", "version"])


def _refusal():
    return failure("PRECONDITION", "Model placement or current operation is unavailable. Refresh and retry.")


def _response(service, row):
    return success(
        ModelSubscriptionOperationType(
            deployment=cluster_model_to_type(service),
            subscription=model_subscription_to_type(row, can_revoke=row.desired_enabled),
        )
    )


def _subscription_environment_operation(args):
    from core.scope_args import read_guid

    guid = read_guid(args, "input.id")
    env_guid = (
        ManagedServiceAttachment.objects.filter(
            guid=guid, model_subscription=True, managed_service__organization_id=current_org_id()
        )
        .values_list("app_environment__guid", flat=True)
        .first()
        if guid
        else None
    )
    return environment_operation("environment_id")({"environment_id": str(env_guid) if env_guid else None})


def _subscribe_model(info, input, *, connection_request=None):
    approvals = None
    if connection_request is not None:
        from astrolift_services.model_connection_requests import eligible_vote_count

        approvals = eligible_vote_count(connection_request, connection_request.app_environment)
    if approvals is None:
        check_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ)({}))
    try:
        model_binding_prefix(input.alias)
    except (TypeError, ValueError):
        return failure(
            "VALIDATION",
            "Subscription alias must start with a lowercase letter and contain up to 32 lowercase letters, digits or underscores.",
        )
    try:
        with transaction.atomic():
            from astrolift_services.model_connection_policy import locked_organization

            locked_organization()
            lookup = _ModelIdentity(
                input.model_deployment_id,
                input.organization_id,
                input.expected_cluster_id,
                input.expected_provider_id,
            )
            service = _locked_model(lookup)
            if service is None:
                return _refusal()
            if service.version != input.if_match_version:
                return version_mismatch(
                    current_version=service.version,
                    requested_version=input.if_match_version,
                    kind="Model deployment",
                )
            from astrolift_services.native_model_connections import (
                is_bedrock_connection,
                model_connectable,
                verify_source,
            )

            if (
                not _idle(service)
                or service.status != "active"
                or not model_connectable(service)
                or (service.config or {}).get("allow_subscriptions") is not True
            ):
                return _refusal()
            env = (
                _locked_environment(input.app_environment_id)
                if approvals is None
                else _locked_environment(input.app_environment_id, approvals=approvals)
            )
            if env is None or env.tenant_cluster_id != service.tenant_cluster_id:
                return _refusal()
            if not allows_app(service, env.registered_app):
                return failure(
                    "PRECONDITION", "This model is dedicated to another app or its app is unavailable."
                )
            from astrolift_services.model_connection_policy import effective_policy
            from astrolift_services.model_connection_requests import source_current

            if not source_current(service):
                return failure("PRECONDITION", "Reviewed model source is unavailable.")
            policy = effective_policy(service)
            if policy.mode == "DENY" or (approvals is None and policy.mode != "AUTO"):
                return failure(
                    "PRECONDITION", "Model connections require approval or are denied by organization policy."
                )
            if env.version != input.if_match_environment_version:
                return version_mismatch(
                    current_version=env.version,
                    requested_version=input.if_match_environment_version,
                    kind="App environment",
                )
            from astrolift_services.model_subscriptions import validate_destination

            validate_destination(env, input.alias)
            if service.attachments.filter(model_subscription=True, desired_enabled=True).count() >= 64:
                return failure("PRECONDITION", "This model has reached its supported subscription limit.")
            prior = (
                ManagedServiceAttachment.objects.select_for_update()
                .filter(model_subscription=True, app_environment=env, binding_alias=input.alias)
                .first()
            )
            # This is the last potentially blocking attachment lock. Re-admit source,
            # policy and the current durable human quorum after it, before any effect.
            if is_bedrock_connection(service):
                verify_source(service)
            if connection_request is None:
                _recheck_authority(info, Permission.APP_UPDATE, service.tenant_cluster, environment=env)
                if not source_current(service) or effective_policy(service).mode != "AUTO":
                    return failure("PRECONDITION", "Model source or connection policy changed. Review again.")
            else:
                from astrolift_services.model_connection_requests import connection_effect_checkpoint

                if not connection_effect_checkpoint(info, connection_request, service, env):
                    return failure("PRECONDITION", "Model connection review is no longer current.")
            if prior:
                if prior.subscription_status != "revoked" or prior.desired_enabled:
                    return failure(
                        "CONFLICT",
                        "This alias already belongs to a model subscription; revoke it before reusing it.",
                    )
                prior.soft_delete()
            service.subscription_revision += 1
            service.save(update_fields=["subscription_revision", "updated_at", "version"])
            row = ManagedServiceAttachment(
                managed_service=service,
                app_environment=env,
                model_subscription=True,
                binding_alias=input.alias,
                desired_enabled=True,
                subscription_status="pending",
                desired_revision=service.subscription_revision,
                reconcile_started_at=timezone.now(),
            )
            row.credential_ref = (
                ""
                if is_bedrock_connection(service)
                else f"services/{service.organization.guid}/{service.guid}/subscriptions/{row.guid}#api_key"
            )
            row.save()
            _enqueue(info, service)
            return _response(service, row)
    except (TypeError, ValueError) as exc:
        return failure("VALIDATION", str(exc))
    except IntegrityError:
        return failure("CONFLICT", "This alias already belongs to a model subscription.")
    except ModelOperationUnavailable:
        return failure(
            "PRECONDITION",
            "Model reconciliation could not be queued. No change was committed; retry later.",
        )


@strawberry.type
class ClusterModelMutations:
    @strawberry.field
    @model_mutation_audit(action="model.provision")
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
    def provision_cluster_model(
        self, info: Info, input: ProvisionClusterModelInput
    ) -> MutationResultType[ClusterModelDeploymentType]:
        if not in_current_org(input.organization_id):
            return _refusal()
        try:
            with transaction.atomic():
                from astrolift_identity.models import Organization

                org_id = current_org_id()
                if not Organization.objects.select_for_update().filter(pk=org_id).exists():
                    return _refusal()
                cluster = _locked_cluster(input.cluster_id, input.expected_provider_id)
                if cluster is None:
                    return _refusal()
                from astrolift_services.hf_connection import credential, verified_model
                from astrolift_services.schema.hf_connections import locked_connection, require_host_admin

                require_host_admin(info, cluster)
                if input.local_artifact_id is not None and (
                    input.connection_id is not None or input.expected_connection_version is not None
                ):
                    return failure("VALIDATION", "A local model source cannot use a Hugging Face connection.")
                connection = (
                    locked_connection(input.connection_id, input.expected_connection_version)
                    if input.connection_id is not None
                    else None
                )
                if connection is None and input.expected_connection_version is not None:
                    return failure("PRECONDITION", "Select a current Hugging Face connection.")
                require_host_admin(info, cluster)
                config, _ = validate_cluster_request(input, cluster, lock_source=True)
                from astrolift_drivers.managed_resolution import resolve_managed_driver

                resolve_managed_driver(
                    cluster_plugin_slug=cluster.provider_plugin.slug, kind="model_endpoint", variant="vllm"
                )
                if input.local_artifact_id is None:
                    verified_model(
                        input.model_repo,
                        input.revision_sha,
                        token=credential(connection) if connection is not None else None,
                        checkpoint=lambda: require_host_admin(info, cluster),
                    )
                require_host_admin(info, cluster)
                tenant = get_current_tenant()
                service = ManagedService.objects.create(
                    organization_id=current_org_id(),
                    tenant_cluster=cluster,
                    kind="model_endpoint",
                    variant="vllm",
                    name=input.name.strip(),
                    config=config,
                    model_hf_connection=connection,
                    model_hf_connection_version=connection.version if connection else None,
                    created_by_id=tenant.actor_user_id if tenant else None,
                )
                if connection is not None:
                    service.config["hf_token_secret_ref"] = (
                        f"services/{service.organization.guid}/{service.guid}/huggingface#token"
                    )
                    service.save(update_fields=["config", "updated_at", "version"])
                _enqueue(info, service)
                return success(cluster_model_to_type(service))
        except (TypeError, ValueError) as exc:
            return failure("VALIDATION", str(exc))
        except IntegrityError:
            return failure("CONFLICT", "A shared model with this name already exists on this cluster.")
        except ModelOperationUnavailable:
            return failure(
                "PRECONDITION",
                "Model reconciliation could not be queued. No change was committed; retry later.",
            )

    @strawberry.field
    @model_mutation_audit(action="model.subscribe")
    @require_permission(
        Permission.APP_UPDATE,
        scope=environment_app_scope("input.app_environment_id", permission=Permission.APP_UPDATE),
        operation=environment_operation("input.app_environment_id"),
    )
    @tenant_scoped()
    def subscribe_cluster_model(
        self, info: Info, input: SubscribeClusterModelInput
    ) -> MutationResultType[ModelSubscriptionOperationType]:
        return _subscribe_model(info, input)

    @strawberry.field
    @model_mutation_audit(action="model.revoke_subscription")
    @require_permission(
        Permission.APP_UPDATE,
        scope=app_scope_via(
            "astrolift_services.ManagedServiceAttachment",
            "input.id",
            app_path="app_environment__registered_app",
            permission=Permission.APP_UPDATE,
        ),
        operation=_subscription_environment_operation,
    )
    @tenant_scoped()
    def revoke_model_subscription(
        self, info: Info, input: RevokeModelSubscriptionInput
    ) -> MutationResultType[ModelSubscriptionOperationType]:
        check_permission(Permission.ORG_READ, scope=cluster_model_org_scope(Permission.ORG_READ)({}))
        try:
            with transaction.atomic():
                row_id = (
                    ManagedServiceAttachment.objects.filter(
                        guid=_guid(input.id),
                        model_subscription=True,
                        managed_service__organization_id=current_org_id(),
                    )
                    .values_list("managed_service__guid", flat=True)
                    .first()
                )
                if row_id is None:
                    return _refusal()
                lookup = _ModelIdentity(
                    GUID(str(row_id)),
                    input.organization_id,
                    input.expected_cluster_id,
                    input.expected_provider_id,
                )
                service = _locked_model(lookup)
                if service is None or not _idle(service):
                    return _refusal()
                if service.version != input.if_match_deployment_version:
                    return version_mismatch(
                        current_version=service.version,
                        requested_version=input.if_match_deployment_version,
                        kind="Model deployment",
                    )
                env_guid = (
                    ManagedServiceAttachment.objects.filter(
                        guid=_guid(input.id),
                        managed_service=service,
                        managed_service__organization_id=current_org_id(),
                    )
                    .values_list("app_environment__guid", flat=True)
                    .first()
                )
                env = _locked_environment(env_guid)
                if env is None or env.tenant_cluster_id != service.tenant_cluster_id:
                    return _refusal()
                row = (
                    ManagedServiceAttachment.objects.select_for_update(of=("self",))
                    .select_related("app_environment__registered_app")
                    .filter(
                        guid=_guid(input.id),
                        model_subscription=True,
                        managed_service=service,
                        managed_service__organization_id=current_org_id(),
                    )
                    .first()
                )
                if (
                    row is None
                    or not _environment_rows(Permission.APP_UPDATE)
                    .filter(pk=row.app_environment_id, registered_app__organization_id=current_org_id())
                    .exists()
                ):
                    return _refusal()
                _recheck_authority(info, Permission.APP_UPDATE, service.tenant_cluster, environment=env)
                if row.version != input.if_match_version:
                    return version_mismatch(
                        current_version=row.version,
                        requested_version=input.if_match_version,
                        kind="Subscription",
                    )
                if not row.desired_enabled and row.subscription_status == "revoked":
                    return _response(service, row)
                service.subscription_revision += 1
                service.save(update_fields=["subscription_revision", "updated_at", "version"])
                row.desired_enabled = False
                row.subscription_status = "revoking"
                row.desired_revision = service.subscription_revision
                row.reconcile_error = ""
                row.reconcile_started_at = timezone.now()
                row.save(
                    update_fields=[
                        "desired_enabled",
                        "subscription_status",
                        "desired_revision",
                        "reconcile_error",
                        "reconcile_started_at",
                        "updated_at",
                        "version",
                    ]
                )
                _enqueue(info, service)
                return _response(service, row)
        except ModelOperationUnavailable:
            return failure(
                "PRECONDITION",
                "Model reconciliation could not be queued. No change was committed; retry later.",
            )

    @strawberry.field
    @model_mutation_audit(action="model.update")
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
    def update_cluster_model(
        self, info: Info, input: UpdateClusterModelInput
    ) -> MutationResultType[ClusterModelDeploymentType]:
        try:
            with transaction.atomic():
                service = _locked_model(input)
                if service is None or service.variant != "vllm" or not _idle(service):
                    return _refusal()
                from astrolift_services.schema.hf_connections import require_host_admin

                require_host_admin(info, service.tenant_cluster)
                if service.version != input.if_match_version:
                    return version_mismatch(
                        current_version=service.version,
                        requested_version=input.if_match_version,
                        kind="Model deployment",
                    )
                placement = update_placement(service, input)
                config, _ = validate_updated_source(service, placement, locked=True)
                config.update(sharing_config(service, input, locked=True))
                if config["sharing_mode"] == "shared":
                    config.pop("dedicated_app_id", None)
                require_host_admin(info, service.tenant_cluster)
                service.name = placement.name.strip()
                service.config = config
                service.subscription_revision += 1
                service.save(
                    update_fields=["name", "config", "subscription_revision", "updated_at", "version"]
                )
                _enqueue(info, service)
                return success(cluster_model_to_type(service))
        except (TypeError, ValueError) as exc:
            return failure("VALIDATION", str(exc))
        except IntegrityError:
            return failure("CONFLICT", "A model with this name already exists on this cluster.")
        except ModelOperationUnavailable:
            return failure(
                "PRECONDITION",
                "Model reconciliation could not be queued. No change was committed; retry later.",
            )

    @strawberry.field
    @model_mutation_audit(action="model.deprovision")
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
    def deprovision_cluster_model(
        self, info: Info, input: DeprovisionClusterModelInput
    ) -> MutationResultType[ClusterModelDeploymentType]:
        try:
            with transaction.atomic():
                service = _locked_model(input)
                if service is None or service.variant != "vllm" or not _idle(service):
                    return _refusal()
                from astrolift_services.schema.hf_connections import require_host_admin

                require_host_admin(info, service.tenant_cluster)
                if service.version != input.if_match_version:
                    return version_mismatch(
                        current_version=service.version,
                        requested_version=input.if_match_version,
                        kind="Model deployment",
                    )
                if (
                    service.attachments.filter(model_subscription=True)
                    .exclude(subscription_status="revoked", desired_enabled=False)
                    .exists()
                ):
                    return failure(
                        "PRECONDITION",
                        "Revoke every subscription and confirm reconciliation before deleting this model.",
                    )
                service.subscription_revision += 1
                service.save(update_fields=["subscription_revision", "updated_at", "version"])
                _enqueue(info, service, action="delete", delete_data=input.delete_data)
                return success(cluster_model_to_type(service))
        except ModelOperationUnavailable:
            return failure(
                "PRECONDITION",
                "Model reconciliation could not be queued. No change was committed; retry later.",
            )
