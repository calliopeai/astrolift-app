"""Original HTTP authority handoff for the staged Endpoint-only deployment path."""

import hashlib
import hmac
import json
from dataclasses import asdict

from django.conf import settings
from django.db.models import Q

from astrolift_lifecycle.models import AppEnvironment, Deployment, DeploymentIdentityOrigin
from astrolift_services.models import (
    GCPGKEPreparationJournal,
    GCPWorkloadIdentityJournal,
    ManagedService,
    ManagedServiceAttachment,
)
from astrolift_services.native_identity_authority import (
    capture_app_identity_authority,
    current_app_identity_authority,
)
from astrolift_workflows.inputs import Actor
from astrolift_workflows.native_identity_inputs import (
    AcceptedAppIdentityAuthority,
    DeploymentAuthorityContext,
)
from core.permissions import Permission, PermissionDenied


class DeploymentOriginError(PermissionDenied):
    """Fixed metadata-only refusal reasons."""

    def __init__(self, reason):
        super().__init__(Permission.APP_DEPLOY, None, reason)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _binding(deployment, authority_sha256, key):
    payload = ":".join(("astrolift.deployment-origin.v1", str(deployment.guid), authority_sha256))
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def native_origin_required(app, environment=None, *, tenant_cluster=None):
    """Include retained ownership and the complete consumer graph, including tombstones."""
    cluster_id = (
        tenant_cluster.pk
        if tenant_cluster is not None
        else (environment.tenant_cluster_id if environment is not None else None)
    )
    journals = GCPWorkloadIdentityJournal.all_objects.filter(registered_app=app)
    preparation = GCPGKEPreparationJournal.all_objects.filter(registered_app=app)
    environments = AppEnvironment.all_objects.filter(registered_app=app)
    if cluster_id is not None:
        journals = journals.filter(tenant_cluster_id=cluster_id)
        preparation = preparation.filter(tenant_cluster_id=cluster_id)
        environments = environments.filter(tenant_cluster_id=cluster_id)
    # Protected preparation operations cannot survive without their PROTECT parent.
    if journals.exists() or preparation.exists():
        return True
    attachments = ManagedServiceAttachment.all_objects.filter(app_environment__in=environments)
    services = ManagedService.all_objects.filter(
        Q(app_environment__in=environments) | Q(pk__in=attachments.values("managed_service_id"))
    )
    return (
        services.filter(kind="model_endpoint")
        .filter(
            Q(variant="vertex_ai")
            | Q(backend_ref__startswith="model_endpoint/projects/")
            | Q(variant="", app_environment__tenant_cluster__provider_plugin__slug="gcp")
            | Q(variant="", tenant_cluster__provider_plugin__slug="gcp")
        )
        .exists()
    )


def capture_deployment_origin(request, app, environment):
    if not native_origin_required(app, environment):
        return None
    if environment.tenant_cluster.provider_plugin.slug != "gcp":
        raise DeploymentOriginError("NATIVE_ORIGIN_PLACEMENT_UNSUPPORTED")
    environments = AppEnvironment.objects.filter(
        registered_app=app, tenant_cluster_id=environment.tenant_cluster_id
    )
    attachments = ManagedServiceAttachment.objects.filter(app_environment__in=environments)
    services = ManagedService.all_objects.filter(
        Q(app_environment__in=environments, deleted_at__isnull=True)
        | Q(pk__in=attachments.values("managed_service_id"))
    )
    if services.exclude(kind="model_endpoint", variant__in=("", "vertex_ai")).exists():
        raise DeploymentOriginError("NATIVE_SERVICE_UNION_UNSUPPORTED")
    env_rows = list(environments.values_list("pk", "tenant_cluster_id")[:65])
    source_rows = list(services.select_related("project__organization")[:65])
    if len(env_rows) > 64 or len(source_rows) > 64 or attachments.count() > 64:
        raise DeploymentOriginError("NATIVE_SERVICE_UNION_BOUND_EXCEEDED")
    env_ids = {pk for pk, cluster in env_rows}
    for service in source_rows:
        direct = (
            service.registered_app_id == app.pk
            and service.app_environment_id in env_ids
            and service.project_id is None
            and service.organization_id is None
            and service.tenant_cluster_id is None
        )
        project_owned = (
            service.registered_app_id is None
            and service.app_environment_id is None
            and service.organization_id is None
            and app.project_id is not None
            and service.project_id == app.project_id
            and service.project.deleted_at is None
            and service.project.organization_id == app.organization_id
            and service.tenant_cluster_id == environment.tenant_cluster_id
        )
        if not (direct or project_owned):
            raise DeploymentOriginError("NATIVE_SERVICE_UNION_UNSUPPORTED")
    return capture_app_identity_authority(
        request, environment_guid=environment.guid, permission=Permission.APP_DEPLOY
    )


def refuse_unsupported_origin(app, environment=None):
    if native_origin_required(app, environment):
        raise DeploymentOriginError("NATIVE_HUMAN_ORIGIN_REQUIRED")


def persist_deployment_origin(deployment, reference):
    if reference is None:
        return
    if not (
        str(deployment.registered_app.guid) == reference.app_guid
        and str(deployment.app_environment.guid) == reference.environment_guid
        and str(deployment.registered_app.organization.guid) == reference.organization_guid
    ):
        raise DeploymentOriginError("DEPLOYMENT_ORIGIN_TARGET_CHANGED")
    with current_app_identity_authority(reference):
        payload = asdict(reference)
        digest = _digest(payload)
        DeploymentIdentityOrigin.objects.create(
            deployment=deployment,
            organization_id=deployment.registered_app.organization_id,
            authority_reference=payload,
            authority_sha256=digest,
            deployment_binding=_binding(deployment, digest, settings.SECRET_KEY),
        )


def deployment_origin(deployment):
    row = DeploymentIdentityOrigin.all_objects.filter(deployment=deployment).first()
    if row is None:
        if native_origin_required(deployment.registered_app, deployment.app_environment):
            raise DeploymentOriginError("DEPLOYMENT_ORIGIN_REQUIRED")
        return None
    payload = row.authority_reference
    if not isinstance(payload, dict) or row.deleted_at is not None:
        raise DeploymentOriginError("DEPLOYMENT_ORIGIN_INVALID")
    digest = _digest(payload)
    keys = (settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", ()))
    if not (
        row.organization_id == deployment.registered_app.organization_id
        and hmac.compare_digest(row.authority_sha256, digest)
        and any(
            hmac.compare_digest(row.deployment_binding, _binding(deployment, digest, key)) for key in keys
        )
    ):
        raise DeploymentOriginError("DEPLOYMENT_ORIGIN_INVALID")
    try:
        reference = AcceptedAppIdentityAuthority(
            **{**payload, "accepted_token_scopes": tuple(payload["accepted_token_scopes"])}
        )
    except (KeyError, TypeError, ValueError):
        raise DeploymentOriginError("DEPLOYMENT_ORIGIN_INVALID") from None
    if (
        str(deployment.registered_app.guid) != reference.app_guid
        or str(deployment.app_environment.guid) != reference.environment_guid
        or str(deployment.registered_app.organization.guid) != reference.organization_guid
    ):
        raise DeploymentOriginError("DEPLOYMENT_ORIGIN_TARGET_CHANGED")
    return reference


def original_actor(reference):
    return Actor(kind="user", user_id=reference.actor_user_id)


def approval_context(reference, deployment_guid):
    """Only an exact protected receipt can supply the recorded deployment's approval facts."""
    from astrolift_identity.operation_context import deployment_approval_count

    deployment = (
        Deployment.objects.select_related("registered_app__organization", "app_environment")
        .filter(guid=deployment_guid)
        .first()
    )
    if deployment is None or deployment_origin(deployment) != reference:
        raise DeploymentOriginError("DEPLOYMENT_ORIGIN_INVALID")
    count = deployment_approval_count(deployment)
    received = deployment.approval_votes.count() if deployment.approvals_required <= 1 else count
    pending = (
        deployment.status == Deployment.Status.PENDING_APPROVAL.value
        and received < deployment.approvals_required
    )
    return count, pending


def deployment_app_identity_authority(reference, context):
    """Explicit consumer handoff; every entry reloads exact receipt and recorded votes.

    This context applies only to reference.environment_guid. A complete app union
    must evaluate each different environment with the ordinary zero-vote authority
    path; it must not pass this context to a re-signed sibling reference.
    """
    if not isinstance(context, DeploymentAuthorityContext):
        raise DeploymentOriginError("DEPLOYMENT_ORIGIN_INVALID")
    return current_app_identity_authority(reference, deployment_guid=context.deployment_guid)
