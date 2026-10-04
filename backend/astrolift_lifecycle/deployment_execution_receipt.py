"""Bind once to trusted activity runtime metadata; never consult the run mirror."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from django.db import transaction
from temporalio import activity

from astrolift_lifecycle.deployment_identity_origin import (
    deployment_app_identity_authority,
    deployment_origin,
)
from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt, DeploymentIdentityOrigin
from astrolift_workflows.inputs import DeployAppInput
from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext


class DeploymentExecutionError(ValueError):
    def __init__(self):
        super().__init__("DEPLOYMENT_EXECUTION_REFUSED")


@dataclass(frozen=True, slots=True)
class BoundDeploymentExecution:
    deployment_guid: str
    operation_uuid: str
    workflow_id: str
    run_id: str
    input_sha256: str
    receipt_guid: str


def normalized_input_sha256(input):
    if not isinstance(input, DeployAppInput) or input.identity_authority is None:
        raise DeploymentExecutionError()
    # Canonical serialization matches Temporal's pure dataclass wire, including
    # the original signed reference; neither the payload nor private config is stored.
    raw = json.dumps(asdict(input), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 65536:
        raise DeploymentExecutionError()
    return hashlib.sha256(b"astrolift.deployment-execution.input.v1\0" + raw).hexdigest()


def expect_deployment_execution(deployment, workflow_type, workflow_id, args):
    if not transaction.get_connection().in_atomic_block:
        raise DeploymentExecutionError()
    canonical_id = f"DeployAppWorkflow-{deployment.registered_app.guid}-{deployment.app_environment.guid}"
    if workflow_id != canonical_id:
        raise DeploymentExecutionError()
    if workflow_type != "DeployAppWorkflow" or len(args) != 1 or not 0 < len(workflow_id) <= 256:
        raise DeploymentExecutionError()
    input = args[0]
    reference = deployment_origin(deployment)
    if (
        reference is None
        or input.identity_authority != reference
        or (input.deployment_id, input.registered_app_id, input.app_environment_id)
        != (deployment.pk, deployment.registered_app_id, deployment.app_environment_id)
    ):
        raise DeploymentExecutionError()
    origin = DeploymentIdentityOrigin.all_objects.get(deployment=deployment)
    digest = normalized_input_sha256(input)
    row, _ = DeploymentExecutionReceipt.all_objects.get_or_create(
        deployment=deployment,
        kind=DeploymentExecutionReceipt.Kind.EXPECTED,
        defaults={
            "organization_id": origin.organization_id,
            "origin": origin,
            "workflow_type": workflow_type,
            "workflow_id": workflow_id,
            "input_sha256": digest,
            "operation_uuid": uuid4(),
        },
    )
    if (
        row.deleted_at is not None
        or row.origin_id != origin.pk
        or row.organization_id != origin.organization_id
        or row.workflow_id != workflow_id
        or row.workflow_type != workflow_type
        or row.input_sha256 != digest
    ):
        raise DeploymentExecutionError()
    return row


@contextmanager
def admitted_deployment_execution(input, *, bind=False):
    """Every invocation re-admits the original caller and exact actual run.

    Only the first registered activity may bind. Same-run retries/replay reuse
    the receipt; reset/continue-as-new/new runs require a new Deployment intent.
    """
    info = activity.info()
    try:
        run_id = str(UUID(info.workflow_run_id))
    except (ValueError, TypeError, AttributeError):
        raise DeploymentExecutionError() from None
    if run_id != info.workflow_run_id:
        raise DeploymentExecutionError()
    deployment = (
        Deployment.objects.select_related("registered_app__organization", "app_environment")
        .filter(
            pk=input.deployment_id,
            registered_app_id=input.registered_app_id,
            app_environment_id=input.app_environment_id,
        )
        .first()
    )
    if deployment is None or deployment_origin(deployment) != input.identity_authority:
        raise DeploymentExecutionError()
    with deployment_app_identity_authority(
        input.identity_authority, DeploymentAuthorityContext(str(deployment.guid))
    ):
        with transaction.atomic():
            expected = (
                DeploymentExecutionReceipt.all_objects.select_for_update()
                .filter(
                    deployment=deployment,
                    kind=DeploymentExecutionReceipt.Kind.EXPECTED,
                )
                .first()
            )
            if (
                expected is None
                or expected.deleted_at is not None
                or expected.origin_id != deployment.identity_origin.pk
                or expected.organization_id != deployment.registered_app.organization_id
                or expected.workflow_id != info.workflow_id
                or expected.workflow_type != info.workflow_type
                or expected.input_sha256 != normalized_input_sha256(input)
            ):
                raise DeploymentExecutionError()
            with deployment_app_identity_authority(
                input.identity_authority, DeploymentAuthorityContext(str(deployment.guid))
            ):
                pass
            deployment.refresh_from_db(fields=["status"])
            if deployment.status not in ("pending", "deploying"):
                raise DeploymentExecutionError()
            row = DeploymentExecutionReceipt.all_objects.filter(
                deployment=deployment,
                kind=DeploymentExecutionReceipt.Kind.BOUND,
            ).first()
            if row is None and bind:
                row = DeploymentExecutionReceipt.objects.create(
                    deployment=deployment,
                    organization_id=expected.organization_id,
                    origin_id=expected.origin_id,
                    expected=expected,
                    kind=DeploymentExecutionReceipt.Kind.BOUND,
                    workflow_id=expected.workflow_id,
                    workflow_type=expected.workflow_type,
                    run_id=run_id,
                    input_sha256=expected.input_sha256,
                    operation_uuid=expected.operation_uuid,
                )
            if (
                row is None
                or row.deleted_at is not None
                or row.expected_id != expected.pk
                or row.origin_id != expected.origin_id
                or row.organization_id != expected.organization_id
                or row.workflow_id != expected.workflow_id
                or row.workflow_type != expected.workflow_type
                or row.run_id != run_id
                or row.input_sha256 != expected.input_sha256
                or row.operation_uuid != expected.operation_uuid
            ):
                raise DeploymentExecutionError()
            execution = BoundDeploymentExecution(
                str(deployment.guid),
                str(row.operation_uuid),
                row.workflow_id,
                row.run_id,
                row.input_sha256,
                str(row.guid),
            )
        yield execution


@contextmanager
def execution_failure_admission(input):
    """Failure metadata cannot mutate a different accepted payload or actual run."""
    info = activity.info()
    try:
        digest = normalized_input_sha256(input)
    except (DeploymentExecutionError, TypeError, ValueError):
        yield False
        return
    with transaction.atomic():
        expected = (
            DeploymentExecutionReceipt.all_objects.select_for_update()
            .filter(
                deployment_id=input.deployment_id,
                kind=DeploymentExecutionReceipt.Kind.EXPECTED,
                deleted_at__isnull=True,
                workflow_id=info.workflow_id,
                workflow_type=info.workflow_type,
                input_sha256=digest,
            )
            .first()
        )
        if expected is None:
            yield False
            return
        bound = DeploymentExecutionReceipt.all_objects.filter(
            deployment_id=input.deployment_id,
            kind=DeploymentExecutionReceipt.Kind.BOUND,
        ).first()
        yield bound is None or (
            bound.deleted_at is None
            and bound.expected_id == expected.pk
            and bound.run_id == info.workflow_run_id
        )
