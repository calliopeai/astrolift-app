"""Operator settlement of a native GKE journal left SENT/UNKNOWN or incomplete (#2278).

A lost reply, crashed worker or expired heartbeat never proves an old send was
cancelled, so the native pipeline refuses every later Deployment of the app on
that cluster (``PRIOR_SEND_UNRESOLVED`` / ``PRIOR_OPERATION_INCOMPLETE``). This
module is the explicit operator recovery for that state. It never claims the
send was observed and it calls no cloud or cluster API:

- It refuses unless the owning Deployment is terminal (failed, superseded or
  rolled back), and, when a send may be on the wire (SENT/UNKNOWN), until the
  journal has not moved for ``GUARD``, which exceeds the native activity's
  60 minute budget and every bounded RPC deadline. A teardown's grant removal
  has no Deployment: it is settled only while the app is tearing down.
- It takes the same advisory mutexes as the executor, without waiting, so it
  cannot race a live worker.
- Preparation: pending intents are dropped; recorded objects keep their UIDs.
  An object a dropped create may have made is not adopted later: the next
  Deployment refuses ``EXISTING_SUBJECT_UID_UNRECORDED`` until the operator
  removes it (the dropped paths are recorded).
- IAM: grants a pending write or removal may have left are folded into the
  owned set. They belong to the app's own GSA and KSA principals, so the next
  reconcile removes any the current union no longer wants.
- Apply: the pending intent is dropped; recorded resources keep their UIDs.

Each settled journal becomes ``RESOLVED`` with one append-only
``GCPNativeJournalResolution`` row naming the operator and their attestation.
"""

import hashlib
import json
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import timedelta

from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from gcp.gke_app_apply import ApplyLedger, ledger_from_payload, ledger_payload
from gcp.gke_identity_preparation import (
    GKEPreparationLedger,
    PreparationIntent,
    PreparationPhase,
    PreparedObject,
)
from gcp.gke_preparation_codec import preparation_ledger_payload
from gcp.identity_owned import (
    OwnedGrantLedger,
    PolicyOwnership,
    owned_ledger_from_payload,
    owned_ledger_payload,
)

from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt
from astrolift_registry.models import RegisteredApp
from astrolift_services.gcp_workload_identity_journal import removal_authority_sha256
from astrolift_services.models import (
    GCPGKEAppApplyJournal,
    GCPGKEAppApplyOperation,
    GCPGKEPreparationJournal,
    GCPGKEPreparationOperation,
    GCPNativeJournalResolution,
    GCPWorkloadIdentityJournal,
)

#: Longer than the native activity's 60 minute start-to-close plus RPC deadlines.
GUARD = timedelta(hours=2)
_TERMINAL = frozenset(
    {Deployment.Status.FAILED.value, Deployment.Status.SUPERSEDED.value, Deployment.Status.ROLLED_BACK.value}
)
_SETTLED = {
    GCPGKEPreparationJournal: ("OBSERVED", "RESOLVED"),
    GCPWorkloadIdentityJournal: ("OBSERVED", "RESOLVED"),
    GCPGKEAppApplyJournal: ("OBSERVED", "REJECTED", "RESOLVED"),
}


class JournalResolutionError(ValueError):
    """Fixed reasons only."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _lock_id(*identity):
    return int.from_bytes(hashlib.sha256(json.dumps(identity).encode()).digest()[:8], "big", signed=True)


@contextmanager
def _mutex(lock_id):
    """The executor's own advisory mutex, tried once; a live worker means busy."""
    mutex = connection.Database.connect(**connection.get_connection_params())
    try:
        mutex.autocommit = True
        with mutex.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            if not cursor.fetchone()[0]:
                raise JournalResolutionError("JOURNAL_BUSY")
        yield
        with mutex.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
    finally:
        mutex.close()


def unresolved_journals(app, cluster):
    """Every native journal of (app, cluster) a later Deployment would refuse on."""
    rows = []
    for model, settled in _SETTLED.items():
        for row in model.all_objects.filter(registered_app=app, tenant_cluster=cluster).order_by("pk"):
            if row.state not in settled or row.deleted_at is not None:
                rows.append(row)
    return rows


def _owning_deployment(row):
    removal = removal_authority_sha256(
        str(row.organization.guid), str(row.registered_app.guid), str(row.tenant_cluster.guid)
    )
    if isinstance(row, GCPWorkloadIdentityJournal) and row.authority_reference_sha256 == removal:
        # An app teardown's grant removal (#2278) has no Deployment; the
        # teardown owns it, and the guard window covers a send on the wire.
        if row.registered_app.provisioning_status != RegisteredApp.ProvisioningStatus.TEARING_DOWN:
            raise JournalResolutionError("OPERATION_OWNER_UNAVAILABLE")
        return None
    receipt = (
        DeploymentExecutionReceipt.all_objects.filter(
            operation_uuid=row.operation_id, kind=DeploymentExecutionReceipt.Kind.EXPECTED
        )
        .select_related("deployment")
        .first()
    )
    if receipt is None or receipt.deployment.registered_app_id != row.registered_app_id:
        raise JournalResolutionError("OPERATION_OWNER_UNAVAILABLE")
    if receipt.deployment.status not in _TERMINAL:
        raise JournalResolutionError("OPERATION_STILL_ACTIVE")
    return receipt.deployment


def _preparation(row):
    payload = row.ledger
    try:
        ledger = GKEPreparationLedger(
            payload["target_sha256"],
            tuple(PreparedObject(**item) for item in payload["objects"]),
            tuple(
                PreparationIntent(**{**item, "phase": PreparationPhase(item["phase"])})
                for item in payload["pending"]
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise JournalResolutionError("JOURNAL_INTEGRITY_FAILURE") from None
    if ledger.sha256 != row.ledger_sha256:
        raise JournalResolutionError("JOURNAL_INTEGRITY_FAILURE")
    dropped = [
        {"kind": i.kind, "path": i.path, "method": i.method, "phase": str(i.phase)} for i in ledger.pending
    ]
    settled = replace(ledger, pending=())
    return preparation_ledger_payload(settled), settled.sha256, dropped


def _iam(row):
    if _hash(row.ledger) != row.ledger_sha256:
        raise JournalResolutionError("JOURNAL_INTEGRITY_FAILURE")
    try:
        ledger = owned_ledger_from_payload(row.ledger)
    except ValueError:
        raise JournalResolutionError("JOURNAL_INTEGRITY_FAILURE") from None
    owned = {item.resource: set(item.grants) for item in ledger.policies}
    dropped = []
    for intent in ledger.pending:
        owned.setdefault(intent.resource, set()).update(intent.owned_after)
        dropped.append({"resource": intent.resource, "phase": str(intent.submission_phase or "")})
    for removal in ledger.removals:
        owned.setdefault(removal.resource, set()).update(removal.grants)
        dropped.append({"resource": removal.resource, "phase": "REMOVAL"})
    settled = OwnedGrantLedger(
        ledger.context_sha256,
        tuple(PolicyOwnership(resource, tuple(sorted(grants))) for resource, grants in sorted(owned.items())),
    )
    payload = owned_ledger_payload(settled)
    return payload, _hash(payload), dropped


def _apply(row):
    try:
        ledger = ledger_from_payload(row.ledger)
    except Exception:
        raise JournalResolutionError("JOURNAL_INTEGRITY_FAILURE") from None
    if ledger.sha256 != row.ledger_sha256:
        raise JournalResolutionError("JOURNAL_INTEGRITY_FAILURE")
    dropped = []
    if ledger.pending is not None:
        resource = ledger.pending.resource
        dropped.append(
            {
                "kind": resource.kind,
                "namespace": resource.namespace,
                "name": resource.name,
                "method": ledger.pending.method,
                "phase": str(ledger.pending.phase),
            }
        )
    settled = ApplyLedger(ledger.resources)
    return ledger_payload(settled), settled.sha256, dropped


def resolve_native_journals(app, cluster, *, user, attestation, now=None):
    """Settle every unresolved native journal of (app, cluster); all or none."""
    attestation = (attestation or "").strip()
    if not 20 <= len(attestation) <= 500:
        raise JournalResolutionError("ATTESTATION_REQUIRED")
    if user is None or not user.is_active:
        raise JournalResolutionError("OPERATOR_REQUIRED")
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise JournalResolutionError("COMMITTED_POSTGRESQL_REQUIRED")
    now = now or timezone.now()
    app_guid, cluster_guid = str(app.guid), str(cluster.guid)
    namespaces = sorted(
        GCPGKEAppApplyJournal.all_objects.filter(registered_app=app, tenant_cluster=cluster).values_list(
            "namespace", flat=True
        )
    )
    with ExitStack() as locks:
        # The executor's order: preparation, IAM, then each app namespace.
        locks.enter_context(_mutex(_lock_id("astrolift-gcp-gke-preparation-v1", app_guid, cluster_guid)))
        locks.enter_context(_mutex(_lock_id("astrolift-gcp-workload-identity-v1", app_guid, cluster_guid)))
        for namespace in namespaces:
            locks.enter_context(
                _mutex(_lock_id("astrolift-gcp-app-apply-v1", app_guid, cluster_guid, namespace))
            )
        try:
            with transaction.atomic():
                return _resolve_locked(app, cluster, user, attestation, now)
        except DatabaseError:
            raise JournalResolutionError("JOURNAL_BUSY") from None


def _resolve_locked(app, cluster, user, attestation, now):
    resolved = []
    for model, settled in _SETTLED.items():
        for row in (
            model.all_objects.select_for_update(nowait=True)
            .filter(registered_app=app, tenant_cluster=cluster)
            .order_by("pk")
        ):
            if row.deleted_at is not None:
                raise JournalResolutionError("RETIRED_JOURNAL_REQUIRES_REVIEW")
            if row.state in settled:
                continue
            deployment = _owning_deployment(row)
            prior_state, prior_sha = row.state, row.ledger_sha256
            if model is GCPGKEPreparationJournal:
                kind, (ledger, sha, dropped) = "PREPARATION", _preparation(row)
                operation = GCPGKEPreparationOperation.all_objects.select_for_update(nowait=True).get(
                    journal=row, operation_id=row.operation_id, generation=row.generation
                )
            elif model is GCPWorkloadIdentityJournal:
                kind, (ledger, sha, dropped), operation = "IAM", _iam(row), None
            else:
                kind, (ledger, sha, dropped) = "APPLY", _apply(row)
                operation = GCPGKEAppApplyOperation.all_objects.select_for_update(nowait=True).get(
                    journal=row, operation_id=row.operation_id, generation=row.generation
                )
            # Holding the mutexes proves no worker is inside this journal; it
            # does not prove a request already on the wire cannot still land.
            in_flight = prior_state in ("SENT", "UNKNOWN") or any(
                item["phase"] in ("SENT", "UNKNOWN") for item in dropped
            )
            if in_flight and row.updated_at > now - GUARD:
                raise JournalResolutionError("RESOLUTION_GUARD_WINDOW")
            row.state, row.ledger, row.ledger_sha256 = "RESOLVED", ledger, sha
            row.save()
            if operation is not None:
                # The accepted operation mirrors its journal; its plan never changes.
                operation.state, operation.ledger, operation.ledger_sha256 = "RESOLVED", ledger, sha
                operation.save()
            resolved.append(
                GCPNativeJournalResolution.objects.create(
                    organization_id=row.organization_id,
                    registered_app=app,
                    tenant_cluster=cluster,
                    deployment=deployment,
                    resolved_by=user,
                    created_by=user,
                    kind=kind,
                    journal_guid=row.guid,
                    operation_id=row.operation_id,
                    generation=row.generation,
                    prior_state=prior_state,
                    prior_ledger_sha256=prior_sha,
                    resolved_ledger_sha256=sha,
                    unresolved=dropped,
                    attestation=attestation,
                )
            )
    if not resolved:
        raise JournalResolutionError("NOTHING_TO_RESOLVE")
    return resolved
