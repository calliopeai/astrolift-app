"""Original SENT reply retention only; never current execution or grant authority."""

import hashlib
import json
from dataclasses import asdict
from dataclasses import fields as dataclass_fields
from types import SimpleNamespace

from django.db import DatabaseError, connection, transaction
from gcp.gke_identity_preparation import PreparationIntent
from gcp.identity_acknowledgement import (
    AcknowledgementReceipt,
    PolicyAcknowledgement,
    PreparationAcknowledgement,
    acknowledgement_sha256,
)
from gcp.identity_owned import PolicySubmission, owned_ledger_from_payload

from astrolift_identity.models import Organization
from astrolift_lifecycle.deployment_execution_receipt import BoundDeploymentExecution
from astrolift_lifecycle.models import DeploymentExecutionReceipt
from astrolift_services.gcp_gke_preparation_journal import PreparationReservation
from astrolift_services.gcp_workload_identity_journal import Reservation
from astrolift_services.models import (
    GCPGKEPreparationJournal,
    GCPGKEPreparationOperation,
    GCPIdentityAcknowledgement,
    GCPWorkloadIdentityJournal,
)


class AcknowledgementError(ValueError):
    """Fixed metadata-only failures; authority withdrawal is not reply erasure."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _binding_sha256(
    kind,
    journal_id,
    execution,
    reservation,
    target_sha256,
    union_sha256,
    authority_sha256,
    submission_sha256,
    acknowledgement_sha,
):
    payload = (
        kind,
        journal_id,
        asdict(execution),
        asdict(reservation),
        target_sha256,
        union_sha256,
        authority_sha256,
        submission_sha256,
        acknowledgement_sha,
    )
    return hashlib.sha256(
        b"astrolift.gcp.native-acknowledgement.binding.v1\0"
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _execution(execution, row, *, lock=False):
    if type(execution) is not BoundDeploymentExecution:
        raise AcknowledgementError("ACKNOWLEDGEMENT_EXECUTION_INVALID")
    try:
        receipts = DeploymentExecutionReceipt.all_objects
        if lock:
            receipts = receipts.select_for_update(nowait=True, of=("self",))
        bound = receipts.select_related("deployment", "origin", "expected").get(
            guid=execution.receipt_guid, kind="BOUND"
        )
    except DeploymentExecutionReceipt.DoesNotExist:
        raise AcknowledgementError("ACKNOWLEDGEMENT_EXECUTION_INVALID") from None
    if (
        str(bound.deployment.guid),
        str(bound.operation_uuid),
        bound.workflow_id,
        bound.run_id,
        bound.input_sha256,
    ) != (
        execution.deployment_guid,
        execution.operation_uuid,
        execution.workflow_id,
        execution.run_id,
        execution.input_sha256,
    ) or (
        bound.deleted_at is not None
        or bound.organization_id != row.organization_id
        or bound.origin.deployment_id != bound.deployment_id
        or bound.origin.organization_id != row.organization_id
        or row.authority_reference_sha256 != bound.origin.authority_sha256
        or bound.origin.authority_reference.get("cluster_guid") != str(row.tenant_cluster.guid)
        or bound.origin.authority_reference.get("provider_guid") != str(row.provider_plugin.guid)
        or bound.deployment.registered_app_id != row.registered_app_id
        or str(row.operation_id) != execution.operation_uuid
        or row.workflow_id != execution.workflow_id
        or row.execution_id != execution.run_id
        or bound.expected is None
        or bound.expected.kind != "EXPECTED"
        or bound.expected.deployment_id != bound.deployment_id
        or bound.expected.origin_id != bound.origin_id
        or bound.expected.organization_id != bound.organization_id
        or bound.expected.workflow_type != bound.workflow_type
        or bound.expected.input_sha256 != bound.input_sha256
        or bound.expected.operation_uuid != bound.operation_uuid
        or bound.expected.workflow_id != bound.workflow_id
    ):
        raise AcknowledgementError("ACKNOWLEDGEMENT_EXECUTION_INVALID")
    return bound


def _retain(reservation, acknowledgement, execution):
    prep = type(reservation) is PreparationReservation
    if not prep and type(reservation) is not Reservation:
        raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
    if type(acknowledgement) is not (PreparationAcknowledgement if prep else PolicyAcknowledgement):
        raise AcknowledgementError("ACKNOWLEDGEMENT_TYPE_INVALID")
    model = GCPGKEPreparationJournal if prep else GCPWorkloadIdentityJournal
    try:
        row = model._unscoped.select_for_update(nowait=True).get(guid=reservation.journal_id)
    except model.DoesNotExist:
        raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID") from None
    union_field = "accepted_union_template_sha256" if prep else "desired_union_sha256"
    if (
        str(row.operation_id),
        row.generation,
        str(row.reservation_nonce),
        row.target_sha256,
        getattr(row, union_field),
        row.authority_reference_sha256,
    ) != (
        reservation.operation_id,
        reservation.generation,
        reservation.nonce,
        reservation.target_sha256,
        getattr(reservation, union_field),
        reservation.authority_reference_sha256,
    ):
        raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
    # FK enforcement can otherwise wait on these parents during INSERT. No
    # current-admission claim is made: retained owner/receipt rows are structural.
    Organization._unscoped.select_for_update(nowait=True).get(pk=row.organization_id)
    bound = _execution(execution, row, lock=True)
    accepted = None
    if prep:
        try:
            accepted = GCPGKEPreparationOperation._unscoped.select_for_update(nowait=True).get(
                journal=row, operation_id=row.operation_id, generation=row.generation
            )
        except GCPGKEPreparationOperation.DoesNotExist:
            raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID") from None
        if (
            accepted.organization_id != row.organization_id
            or accepted.reservation_nonce != row.reservation_nonce
            or accepted.accepted_union_template_sha256 != row.accepted_union_template_sha256
            or accepted.authority_reference_sha256 != row.authority_reference_sha256
            or accepted.workflow_id != row.workflow_id
            or accepted.execution_id != row.execution_id
            or accepted.accepted_authority_reference != bound.origin.authority_reference
            or accepted.submissions != row.submissions
            or accepted.ledger != row.ledger
        ):
            raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
        payload = row.ledger
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "target_sha256", "objects", "pending"}
            or payload["schema_version"] != 1
            or payload["target_sha256"] != row.preparation_sha256
            or _hash({key: value for key, value in payload.items() if key != "schema_version"})
            != row.ledger_sha256
            or _hash(row.target_snapshot) != row.target_sha256
            or not isinstance(payload["pending"], list)
            or len(payload["pending"]) > 128
        ):
            raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
        pending = []
        for item in payload["pending"]:
            if (
                type(item) is not dict
                or set(item) != {field.name for field in dataclass_fields(PreparationIntent)}
                or any(type(value) is not str for value in item.values())
            ):
                raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
            pending.append(PreparationIntent(**item))
        ledger = SimpleNamespace(pending=pending)
    else:
        ledger = owned_ledger_from_payload(row.ledger)
        if _hash(row.ledger) != row.ledger_sha256 or _hash(row.target_snapshot) != row.target_sha256:
            raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
    intent = next(
        (item for item in ledger.pending if item.submission_id == acknowledgement.submission_id), None
    )
    history = row.submissions.get(acknowledgement.submission_id)
    if intent is None or not isinstance(history, dict) or history.get("state") != "SENT":
        raise AcknowledgementError("ACKNOWLEDGEMENT_SENT_REQUIRED")
    if prep:
        if (
            intent.phase != "SENT"
            or intent.operation_id != reservation.operation_id
            or intent.request_sha256 != acknowledgement.request_sha256
            or history.get("path") != intent.path
            or history.get("method") != intent.method
            or history.get("record_sha256")
            != _hash((row.preparation_sha256, {**asdict(intent), "phase": "UNSENT"}))
            or (intent.original_uid and acknowledgement.uid != intent.original_uid)
        ):
            raise AcknowledgementError("ACKNOWLEDGEMENT_SUBMISSION_INVALID")
    else:
        if (
            intent.submission_phase.value != "SENT"
            or history.get("submission_sha256") != acknowledgement.submission_sha256
            or history.get("resource") != intent.resource
            or PolicySubmission(row.context_sha256, intent, ledger).submission_sha256
            != acknowledgement.submission_sha256
        ):
            raise AcknowledgementError("ACKNOWLEDGEMENT_SUBMISSION_INVALID")
    digest = acknowledgement_sha256(acknowledgement)
    fields = {
        "organization": row.organization,
        "execution_receipt": bound,
        "preparation_journal": row if prep else None,
        "preparation_operation": accepted,
        "iam_journal": None if prep else row,
        "reservation_nonce": row.reservation_nonce,
        "generation": row.generation,
        "acknowledgement": asdict(acknowledgement),
        "acknowledgement_sha256": digest,
        "target_sha256": row.target_sha256,
        "accepted_union_sha256": getattr(row, union_field),
        "authority_reference_sha256": row.authority_reference_sha256,
        "submission_sha256": history["record_sha256" if prep else "submission_sha256"],
    }
    fields["binding_sha256"] = _binding_sha256(
        "PREPARATION" if prep else "IAM",
        str(row.guid),
        execution,
        reservation,
        fields["target_sha256"],
        fields["accepted_union_sha256"],
        fields["authority_reference_sha256"],
        fields["submission_sha256"],
        digest,
    )
    saved, created = GCPIdentityAcknowledgement._unscoped.get_or_create(
        kind="PREPARATION" if prep else "IAM",
        operation_id=row.operation_id,
        submission_id=acknowledgement.submission_id,
        defaults=fields,
    )
    foreign_keys = {
        "organization",
        "execution_receipt",
        "preparation_journal",
        "preparation_operation",
        "iam_journal",
    }
    exact_fields = {
        (key + "_id" if key in foreign_keys else key): (value.pk if value is not None else None)
        if key in foreign_keys
        else value
        for key, value in fields.items()
    }
    if not created and any(getattr(saved, key) != value for key, value in exact_fields.items()):
        raise AcknowledgementError("ACKNOWLEDGEMENT_ORIGINAL_REPLY_CHANGED")
    return AcknowledgementReceipt(str(saved.guid), digest)


def retain_acknowledgement(reservation, acknowledgement, *, execution):
    """No current-admission bypass for observation: this appends response metadata only.

    The original journal slot must still exist with its exact SENT reservation.
    Parent/receipt FKs protect physical deletion; parent reassignment or generation
    advancement can prevent retention. No deletion-survival guarantee is made.
    """
    if connection.in_atomic_block or not connection.get_autocommit():
        raise AcknowledgementError("ACKNOWLEDGEMENT_OUTER_TRANSACTION_REFUSED")
    try:
        with transaction.atomic():
            return _retain(reservation, acknowledgement, execution)
    except DatabaseError as error:
        cause = error.__cause__
        if getattr(cause, "sqlstate", None) == "55P03" or getattr(cause, "pgcode", None) == "55P03":
            raise AcknowledgementError("JOURNAL_BUSY") from None
        raise AcknowledgementError("ACKNOWLEDGEMENT_COMMIT_UNCONFIRMED") from None


def retained_acknowledgements(reservation, *, execution):
    """Read protected identities only; callers must separately re-admit every effect."""
    prep = type(reservation) is PreparationReservation
    if not prep and type(reservation) is not Reservation:
        raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
    model = GCPGKEPreparationJournal if prep else GCPWorkloadIdentityJournal
    try:
        row = model._unscoped.get(guid=reservation.journal_id)
    except model.DoesNotExist:
        raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID") from None
    bound = _execution(execution, row)
    if (str(row.reservation_nonce), row.generation) != (reservation.nonce, reservation.generation):
        raise AcknowledgementError("ACKNOWLEDGEMENT_RESERVATION_INVALID")
    key = "preparation_journal" if prep else "iam_journal"
    values = []
    for saved in GCPIdentityAcknowledgement._unscoped.filter(
        **{key: row},
        execution_receipt=bound,
        generation=reservation.generation,
        reservation_nonce=reservation.nonce,
        operation_id=reservation.operation_id,
    ).order_by("submission_id"):
        try:
            value = (PreparationAcknowledgement if prep else PolicyAcknowledgement)(**saved.acknowledgement)
        except (ValueError, TypeError):
            raise AcknowledgementError("ACKNOWLEDGEMENT_INTEGRITY_INVALID") from None
        if (
            acknowledgement_sha256(value) != saved.acknowledgement_sha256
            or str(saved.submission_id) != value.submission_id
        ):
            raise AcknowledgementError("ACKNOWLEDGEMENT_INTEGRITY_INVALID")
        union_sha256 = getattr(
            reservation, "accepted_union_template_sha256" if prep else "desired_union_sha256"
        )
        if (
            saved.target_sha256 != reservation.target_sha256
            or saved.accepted_union_sha256 != union_sha256
            or saved.authority_reference_sha256 != reservation.authority_reference_sha256
            or saved.binding_sha256
            != _binding_sha256(
                saved.kind,
                str(row.guid),
                execution,
                reservation,
                saved.target_sha256,
                saved.accepted_union_sha256,
                saved.authority_reference_sha256,
                saved.submission_sha256,
                saved.acknowledgement_sha256,
            )
        ):
            raise AcknowledgementError("ACKNOWLEDGEMENT_INTEGRITY_INVALID")
        values.append(value)
    return tuple(values)
