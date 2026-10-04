"""Registered first gate; receipt-bound native execution is not installed yet."""

from temporalio import activity
from temporalio.exceptions import ApplicationError

from astrolift_workflows.inputs import DeployAppInput


@activity.defn
def validate_deployment_identity_origin(input: DeployAppInput) -> None:
    from astrolift_lifecycle.deployment_identity_origin import (
        deployment_app_identity_authority,
        deployment_origin,
    )
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext

    deployment = (
        Deployment.objects.select_related(
            "registered_app__organization", "app_environment__tenant_cluster__provider_plugin"
        )
        .filter(pk=input.deployment_id)
        .first()
    )
    if (
        deployment is None
        or deployment.registered_app_id != input.registered_app_id
        or deployment.app_environment_id != input.app_environment_id
    ):
        raise ApplicationError("DEPLOYMENT_ORIGIN_TARGET_CHANGED", non_retryable=True)
    try:
        reference = deployment_origin(deployment)
        if reference != input.identity_authority:
            raise ValueError("DEPLOYMENT_ORIGIN_INPUT_MISMATCH")
        if reference is None:
            return
        with deployment_app_identity_authority(reference, DeploymentAuthorityContext(str(deployment.guid))):
            pass
    except Exception:
        raise ApplicationError("DEPLOYMENT_ORIGIN_REFUSED", non_retryable=True) from None
    raise ApplicationError("NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED", non_retryable=True)


@activity.defn
def mark_deployment_identity_refused(input: DeployAppInput, reason: str) -> None:
    from astrolift_lifecycle.models import Deployment

    row = Deployment.objects.filter(
        pk=input.deployment_id,
        registered_app_id=input.registered_app_id,
        app_environment_id=input.app_environment_id,
    ).first()
    if row is None or row.status in ("running", "failed", "superseded", "rolled_back"):
        return
    row.status = Deployment.Status.FAILED.value
    row.aborted_reason = (
        "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
        if "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED" in reason
        else "DEPLOYMENT_ORIGIN_REFUSED"
    )
    row.save(update_fields=["status", "aborted_reason", "updated_at", "version"])
