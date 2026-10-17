"""Registered first gate for every DeployAppWorkflow history.

An admitted native origin is reported as NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED,
exactly as before: histories without the native pipeline patch refuse on it, and
patched histories route to the Endpoint-only native steps (#2278). Keeping this
activity's signature and outcomes unchanged means a worker of either release can
run it, and no legacy step is ever reached by a native Deployment.
"""

import re

from temporalio import activity
from temporalio.exceptions import ApplicationError

from astrolift_workflows.inputs import DeployAppInput


@activity.defn
def validate_deployment_identity_origin(input: DeployAppInput) -> None:
    from astrolift_lifecycle.deployment_execution_receipt import (
        DeploymentExecutionError,
        admitted_deployment_execution,
    )
    from astrolift_lifecycle.deployment_identity_origin import (
        deployment_origin,
    )
    from astrolift_lifecycle.models import Deployment

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
        with admitted_deployment_execution(input, bind=True):
            pass
    except DeploymentExecutionError:
        raise ApplicationError("DEPLOYMENT_EXECUTION_REFUSED", non_retryable=True) from None
    except Exception:
        raise ApplicationError("DEPLOYMENT_ORIGIN_REFUSED", non_retryable=True) from None
    raise ApplicationError("NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED", non_retryable=True)


@activity.defn
def mark_deployment_identity_refused(input: DeployAppInput, reason: str) -> None:
    from astrolift_lifecycle.deployment_execution_receipt import execution_failure_admission
    from astrolift_lifecycle.models import Deployment

    with execution_failure_admission(input) as admitted:
        if not admitted:
            return
        row = Deployment.objects.filter(
            pk=input.deployment_id,
            registered_app_id=input.registered_app_id,
            app_environment_id=input.app_environment_id,
        ).first()
        if row is None or row.status in ("running", "failed", "superseded", "rolled_back"):
            return
        row.status = Deployment.Status.FAILED.value
        row.aborted_reason = (
            reason
            if re.fullmatch(r"NATIVE_CONFIGURATION_REFUSED:[A-Z][A-Z0-9_]{2,63}", reason)
            else "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
            if "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED" in reason
            else "DEPLOYMENT_EXECUTION_REFUSED"
            if "DEPLOYMENT_EXECUTION_REFUSED" in reason
            else "DEPLOYMENT_ORIGIN_REFUSED"
        )
        row.save(update_fields=["status", "aborted_reason", "updated_at", "version"])
