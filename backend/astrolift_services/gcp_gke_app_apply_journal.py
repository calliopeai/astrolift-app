"""Private committed app apply adapter. No workflow activation or native calls.

The caller holds preparation then IAM then this advisory-only mutex. The current
checkpoint must re-admit the original Deployment and real activity execution,
plus its current source plan. Hashes and a reservation are never authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from django.db import DatabaseError, connection
from django.utils import timezone
from gcp.gke_app_apply import (
    AppApplyReceipt,
    ApplyIntent,
    ApplyLedger,
    ApplyPhase,
    CompiledAppPlan,
    EvidenceReceipt,
    NativeEvidence,
    NativeRejection,
    SubmissionReceipt,
    ledger_from_payload,
    ledger_payload,
)
from gcp.gke_app_runtime_handoff import RuntimeHandoff

from astrolift_lifecycle.deployment_execution_receipt import BoundDeploymentExecution
from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt
from astrolift_services.gcp_gke_preparation_journal import PreparationStore
from astrolift_services.gcp_workload_identity_journal import _outside_atomic
from astrolift_services.models import (
    GCPAppIdentitySource,
    GCPClusterIdentitySource,
    GCPGKEAppApplyJournal,
    GCPGKEAppApplyOperation,
)
from core.gcp_prepared_identity_render import PreparedGCPIdentity, assert_deployment_identity


class AppApplyJournalError(ValueError):
    """Only fixed failure reasons; never retain manifests, credentials or bodies."""


def _json(value):
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _identity(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or not UUID(value).int:
            raise ValueError
    except Exception:
        raise AppApplyJournalError("INVALID_APPLY_IDENTITY") from None


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise AppApplyJournalError("INVALID_APPLY_DIGEST")


@dataclass(frozen=True)
class SourceFence:
    source_id: str
    source_version: int
    cluster_source_id: str
    original_sha256: str
    native_identity_sha256: str

    def __post_init__(self):
        _identity(self.source_id)
        _identity(self.cluster_source_id)
        _digest(self.original_sha256)
        _digest(self.native_identity_sha256)
        if type(self.source_version) is not int or self.source_version < 1:
            raise AppApplyJournalError("INVALID_SOURCE_VERSION")

    @classmethod
    def from_original(cls, original):
        return cls(
            original.source_id,
            original.source_version,
            original.cluster_source_id,
            original.original_sha256,
            original.identity.fingerprint,
        )


@dataclass(frozen=True)
class AcceptedApply:
    plan: CompiledAppPlan
    prepared: PreparedGCPIdentity
    source: SourceFence
    execution: BoundDeploymentExecution

    def __post_init__(self):
        if (
            type(self.plan) is not CompiledAppPlan
            or type(self.prepared) is not PreparedGCPIdentity
            or type(self.source) is not SourceFence
        ):
            raise AppApplyJournalError("INVALID_ACCEPTED_APPLY")
        if (
            self.plan.execution.deployment_id != self.prepared.deployment_guid
            or self.plan.identity_sha256 != self.prepared.fingerprint
        ):
            raise AppApplyJournalError("ACCEPTED_RENDER_CHANGED")
        if type(self.execution) is not BoundDeploymentExecution or (
            self.execution.deployment_guid,
            self.execution.operation_uuid,
            self.execution.workflow_id,
            self.execution.run_id,
        ) != (
            self.plan.execution.deployment_id,
            self.plan.execution.operation_id,
            self.plan.execution.workflow_id,
            self.plan.execution.execution_id,
        ):
            raise AppApplyJournalError("ACCEPTED_EXECUTION_CHANGED")
        if self.plan.execution.operation_id != self.prepared.operation_id or any(
            r.namespace != self.prepared.namespace for r in self.plan.resources
        ):
            raise AppApplyJournalError("ACCEPTED_OPERATION_CHANGED")

    @property
    def original_target(self):
        identity = self.prepared
        return {
            key: getattr(identity, key)
            for key in (
                "organization_guid",
                "app_guid",
                "environment_guid",
                "cluster_guid",
                "provider_guid",
                "namespace",
                "namespace_uid",
                "service_account_name",
                "service_account_uid",
                "gsa_email",
                "gsa_unique_id",
                "preparation_target_sha256",
            )
        }


@dataclass(frozen=True)
class ApplyReservation:
    journal_id: str
    operation_id: str
    nonce: str
    generation: int
    plan_sha256: str

    def __post_init__(self):
        for value in (self.journal_id, self.operation_id, self.nonce):
            _identity(value)
        _digest(self.plan_sha256)
        if type(self.generation) is not int or self.generation < 1:
            raise AppApplyJournalError("INVALID_APPLY_GENERATION")


@dataclass(frozen=True)
class ApplyCheckpoint:
    preparation: object
    deployment: Deployment
    source: GCPAppIdentitySource
    cluster_source: GCPClusterIdentitySource
    journal: GCPGKEAppApplyJournal | None
    operation: GCPGKEAppApplyOperation | None
    accepted: AcceptedApply


Checkpoint = Callable[[ApplyCheckpoint], None]


class AppApplyStore:
    def __init__(
        self,
        accepted,
        mutex,
        lock_id,
        *,
        preparation,
        reservation,
        iam,
        preparation_checkpoint,
        iam_checkpoint,
    ):
        if type(accepted) is not AcceptedApply or type(preparation) is not PreparationStore:
            raise AppApplyJournalError("CURRENT_PREPARATION_REQUIRED")
        if not callable(preparation_checkpoint) or not callable(iam_checkpoint):
            raise AppApplyJournalError("CURRENT_AUTHORITY_REQUIRED")
        self.accepted, self.preparation, self.preparation_reservation, self.iam = (
            accepted,
            preparation,
            reservation,
            iam,
        )
        self.preparation_checkpoint, self.iam_checkpoint = preparation_checkpoint, iam_checkpoint
        self._mutex, self._lock_id, self._active = mutex, lock_id, True

    def _ready(self):
        _outside_atomic()
        if not self._active or self._mutex.closed:
            raise AppApplyJournalError("APPLY_MUTEX_NOT_HELD")
        with self._mutex.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype=%s AND pid=pg_backend_pid() AND classid=%s AND objid=%s AND objsubid=1 AND granted)",
                ["advisory", (self._lock_id >> 32) & 0xFFFFFFFF, self._lock_id & 0xFFFFFFFF],
            )
            if not cursor.fetchone()[0]:
                raise AppApplyJournalError("APPLY_MUTEX_NOT_HELD")

    @contextmanager
    def _locked(self, checkpoint, *, evidence_only=False):
        self._ready()
        if not callable(checkpoint):
            raise AppApplyJournalError("CURRENT_AUTHORITY_REQUIRED")
        prep = self.preparation

        # Evidence-only retention is NOT admission: it checks the exact immutable
        # sent operation under all original ownership locks, and cannot advance
        # observation or permit any subsequent native request.
        def retain_only(context):
            p = self.accepted.prepared
            if str(context.app.guid) != p.app_guid or str(context.cluster.guid) != p.cluster_guid:
                raise AppApplyJournalError("ACKNOWLEDGED_TARGET_CHANGED")

        try:
            with prep._locked(
                prep._operation(self.preparation_reservation),
                retain_only if evidence_only else self.preparation_checkpoint,
                iam=self.iam,
            ) as (row, accepted_prep, iam_row):
                row = prep._bound(row, self.preparation_reservation)
                current = prep._checkpoint_context
                identity, source_fence = self.accepted.prepared, self.accepted.source
                deployment = (
                    Deployment._unscoped.select_for_update(nowait=True)
                    .select_related(
                        "app_environment__tenant_cluster__provider_plugin", "registered_app__organization"
                    )
                    .get(guid=identity.deployment_guid, deleted_at__isnull=True)
                )
                assert_deployment_identity(deployment, current.cluster, identity)
                execution = DeploymentExecutionReceipt.all_objects.get(
                    guid=self.accepted.execution.receipt_guid,
                    deployment=deployment,
                    organization=current.organization,
                    kind="BOUND",
                    deleted_at__isnull=True,
                )
                if (
                    str(execution.operation_uuid),
                    execution.workflow_id,
                    execution.run_id,
                    execution.input_sha256,
                    execution.origin_id,
                ) != (
                    self.accepted.execution.operation_uuid,
                    self.accepted.execution.workflow_id,
                    self.accepted.execution.run_id,
                    self.accepted.execution.input_sha256,
                    deployment.identity_origin.pk,
                ):
                    raise AppApplyJournalError("CURRENT_EXECUTION_CHANGED")
                self._execution_receipt = execution

                source = GCPAppIdentitySource._unscoped.select_for_update(nowait=True).get(
                    guid=source_fence.source_id,
                    organization=current.organization,
                    registered_app=current.app,
                    tenant_cluster=current.cluster,
                    provider_plugin=current.provider,
                )
                cluster_source = GCPClusterIdentitySource._unscoped.select_for_update(nowait=True).get(
                    guid=source_fence.cluster_source_id,
                    tenant_cluster=current.cluster,
                    provider_plugin=current.provider,
                )
                if (
                    source.cluster_source_id != cluster_source.pk
                    or source.unique_id != identity.gsa_unique_id
                ):
                    raise AppApplyJournalError("ORIGINAL_SOURCE_CHANGED")
                if not evidence_only:
                    prep._annotation_current(row, accepted_prep, self.iam, iam_row)
                    self._verify_identity(row, accepted_prep, iam_row, deployment)
                    if (
                        source.deleted_at
                        or source.state != "OBSERVED"
                        or source.observed_at is None
                        or source.version != source_fence.source_version
                        or source.original_sha256 != source_fence.original_sha256
                        or cluster_source.deleted_at
                    ):
                        raise AppApplyJournalError("CURRENT_SOURCE_UNAVAILABLE")
                    if self.iam_checkpoint(self._iam_context(current, iam_row)) is not None:
                        raise AppApplyJournalError("CURRENT_IAM_UNCONFIRMED")
                rows = GCPGKEAppApplyJournal._unscoped.select_for_update(nowait=True).filter(
                    registered_app=current.app, tenant_cluster=current.cluster, namespace=identity.namespace
                )
                journal = rows.first()
                operation = None
                if journal:
                    if (
                        journal.deleted_at
                        or journal.organization_id != current.organization.pk
                        or journal.provider_plugin_id != current.provider.pk
                        or journal.original_target != self.accepted.original_target
                        or journal.original_target_sha256 != _hash(journal.original_target)
                    ):
                        if journal.app_environment_id != deployment.app_environment_id:
                            raise AppApplyJournalError("PHYSICAL_ENVIRONMENT_HANDOFF_REQUIRES_REVIEW")
                        raise AppApplyJournalError("ORIGINAL_APPLY_TARGET_CHANGED")
                    operation = GCPGKEAppApplyOperation._unscoped.select_for_update(nowait=True).get(
                        journal=journal, operation_id=journal.operation_id, generation=journal.generation
                    )
                    if (
                        operation.deleted_at
                        or str(operation.nonce) != str(journal.nonce)
                        or operation.ledger != journal.ledger
                        or operation.ledger_sha256 != journal.ledger_sha256
                        or operation.state != journal.state
                    ):
                        raise AppApplyJournalError("APPLY_OPERATION_INTEGRITY_FAILURE")
                    ledger = ledger_from_payload(journal.ledger)
                    if ledger.sha256 != journal.ledger_sha256:
                        raise AppApplyJournalError("APPLY_LEDGER_INTEGRITY_FAILURE")
                ctx = ApplyCheckpoint(
                    current, deployment, source, cluster_source, journal, operation, self.accepted
                )
                if not evidence_only and checkpoint(ctx) is not None:
                    raise AppApplyJournalError("CURRENT_AUTHORITY_UNCONFIRMED")
                self._context = ctx
                yield journal, operation
        except DatabaseError as error:
            cause = error.__cause__
            if getattr(cause, "sqlstate", None) == "55P03" or getattr(cause, "pgcode", None) == "55P03":
                raise AppApplyJournalError("JOURNAL_BUSY") from None
            raise AppApplyJournalError("APPLY_DATABASE_UNCONFIRMED") from None

    def _iam_context(self, current, row):
        from astrolift_services.gcp_workload_identity_journal import JournalCheckpoint, OperationIdentity

        return JournalCheckpoint(
            current.organization,
            current.team,
            current.project,
            current.app,
            current.cluster,
            current.provider,
            row,
            self.iam[0].target,
            OperationIdentity(
                str(row.operation_id),
                row.workflow_id,
                row.execution_id,
                row.desired_revision,
                row.desired_union_sha256,
                row.authority_reference_sha256,
            ),
        )

    def _verify_identity(self, row, accepted, iam_row, deployment):
        expected = self.accepted.prepared
        if (
            row.state != "OBSERVED"
            or row.observed_revision != row.desired_revision
            or row.observed_at is None
        ):
            raise AppApplyJournalError("PREPARATION_UNOBSERVED")
        if (
            str(row.guid),
            row.version,
            str(row.operation_id),
            row.generation,
            row.target_sha256,
            row.derived_native_union_sha256,
            str(iam_row.guid),
            iam_row.version,
        ) != (
            expected.preparation_journal_guid,
            expected.preparation_journal_version,
            expected.operation_id,
            expected.generation,
            expected.preparation_target_sha256,
            expected.desired_union_sha256,
            expected.iam_journal_guid,
            expected.iam_journal_version,
        ):
            raise AppApplyJournalError("CURRENT_PREPARATION_FENCE_CHANGED")
        op = self.preparation._accepted_operation(accepted, row)
        if op.template.source_snapshot_sha256 != expected.source_snapshot_sha256 or (
            op.workflow_id,
            op.execution_id,
        ) != (self.accepted.plan.execution.workflow_id, self.accepted.plan.execution.execution_id):
            raise AppApplyJournalError("CURRENT_EXECUTION_CHANGED")
        subjects = [
            s for s in op.template.subjects if str(deployment.app_environment.guid) in s.environment_ids
        ]
        if len(subjects) != 1 or (subjects[0].namespace, subjects[0].name) != (
            expected.namespace,
            expected.service_account_name,
        ):
            raise AppApplyJournalError("CURRENT_SUBJECT_CHANGED")
        objects = self.preparation._ledger(row).objects
        ns = next(
            (
                o
                for o in objects
                if o.kind == "Namespace" and o.path == f"/api/v1/namespaces/{expected.namespace}"
            ),
            None,
        )
        sa = next(
            (
                o
                for o in objects
                if o.kind == "ServiceAccount"
                and o.path
                == f"/api/v1/namespaces/{expected.namespace}/serviceaccounts/{expected.service_account_name}"
            ),
            None,
        )
        native = self.preparation.target.context.identity
        if (
            ns is None
            or sa is None
            or (ns.uid, sa.uid, native.email, native.service_account_unique_id, native.fingerprint)
            != (
                expected.namespace_uid,
                expected.service_account_uid,
                expected.gsa_email,
                expected.gsa_unique_id,
                self.accepted.source.native_identity_sha256,
            )
        ):
            raise AppApplyJournalError("ORIGINAL_PREPARED_IDENTITY_CHANGED")

    def _bound(self, journal, operation, reservation):
        if (
            journal is None
            or operation is None
            or type(reservation) is not ApplyReservation
            or (
                str(journal.guid),
                str(journal.operation_id),
                str(journal.nonce),
                journal.generation,
                operation.accepted_plan_sha256,
            )
            != (
                reservation.journal_id,
                reservation.operation_id,
                reservation.nonce,
                reservation.generation,
                reservation.plan_sha256,
            )
        ):
            raise AppApplyJournalError("STALE_APPLY_RESERVATION")
        if (
            operation.accepted_plan != _json(asdict(self.accepted.plan))
            or operation.source_fence != _json(asdict(self.accepted.source))
            or operation.prepared_identity != _json(asdict(self.accepted.prepared))
        ):
            raise AppApplyJournalError("ACCEPTED_EXECUTION_CHANGED")
        return journal, operation

    def _save(self, journal, operation, ledger, state):
        payload = ledger_payload(ledger)
        for row in (journal, operation):
            row.ledger, row.ledger_sha256, row.state = payload, ledger.sha256, state
            row.save()

    def reserve(self, *, checkpoint):
        if self.accepted.plan.placement is None:
            raise AppApplyJournalError("PLACEMENT_ACCEPTANCE_REQUIRED")
        with self._locked(checkpoint) as (journal, prior):
            ctx = self._context
            plan, identity = self.accepted.plan, self.accepted.prepared
            if journal and str(journal.operation_id) == plan.execution.operation_id:
                reservation = ApplyReservation(
                    str(journal.guid),
                    str(journal.operation_id),
                    str(journal.nonce),
                    journal.generation,
                    prior.accepted_plan_sha256,
                )
                self._bound(journal, prior, reservation)
                return reservation
            if journal:
                previous_ledger = ledger_from_payload(journal.ledger)
                definitively_rejected = (
                    journal.state == "REJECTED"
                    and previous_ledger.pending is not None
                    and previous_ledger.pending.phase == ApplyPhase.REJECTED
                    and previous_ledger.rejection is not None
                )
                if not definitively_rejected and (
                    journal.state != "OBSERVED" or journal.observed_at is None or previous_ledger.pending
                ):
                    raise AppApplyJournalError("APPLY_UNRESOLVED_NO_TAKEOVER")
                if {r.resource.path for r in ledger_from_payload(journal.ledger).resources} - {
                    r.path for r in plan.resources
                }:
                    raise AppApplyJournalError("APPLY_RESOURCE_REMOVAL_REQUIRES_REVIEW")
                if definitively_rejected:
                    journal.ledger = ledger_payload(ApplyLedger(previous_ledger.resources))
                    journal.ledger_sha256 = ApplyLedger(previous_ledger.resources).sha256
                journal.generation += 1
            else:
                journal = GCPGKEAppApplyJournal(
                    organization=ctx.preparation.organization,
                    registered_app=ctx.preparation.app,
                    app_environment=ctx.deployment.app_environment,
                    tenant_cluster=ctx.preparation.cluster,
                    provider_plugin=ctx.preparation.provider,
                    namespace=identity.namespace,
                    original_target=self.accepted.original_target,
                    original_target_sha256=_hash(self.accepted.original_target),
                    ledger=ledger_payload(ApplyLedger()),
                    ledger_sha256=ApplyLedger().sha256,
                )
            journal.operation_id, journal.nonce, journal.state = (
                plan.execution.operation_id,
                uuid4(),
                "RESERVED",
            )
            journal.observed_at = None
            journal.save()
            GCPGKEAppApplyOperation.objects.create(
                organization=journal.organization,
                journal=journal,
                deployment=ctx.deployment,
                execution_receipt=self._execution_receipt,
                identity_source=ctx.source,
                preparation_operation=ctx.preparation.accepted,
                operation_id=journal.operation_id,
                nonce=journal.nonce,
                generation=journal.generation,
                workflow_id=plan.execution.workflow_id,
                execution_id=plan.execution.execution_id,
                accepted_plan=_json(asdict(plan)),
                accepted_plan_sha256=plan.sha256,
                source_fence=_json(asdict(self.accepted.source)),
                prepared_identity=_json(asdict(identity)),
                ledger=journal.ledger,
                ledger_sha256=journal.ledger_sha256,
            )
            return ApplyReservation(
                str(journal.guid),
                str(journal.operation_id),
                str(journal.nonce),
                journal.generation,
                plan.sha256,
            )

    def read(self, reservation, *, checkpoint):
        with self._locked(checkpoint) as (row, op):
            row, op = self._bound(row, op, reservation)
            return ledger_from_payload(row.ledger)

    def validate_current(self, reservation, *, checkpoint):
        self.read(reservation, checkpoint=checkpoint)

    def commit_submission(self, reservation, intent, *, checkpoint):
        if type(intent) is not ApplyIntent or intent.phase not in (ApplyPhase.UNSENT, ApplyPhase.SENT):
            raise AppApplyJournalError("INVALID_APPLY_SUBMISSION")
        with self._locked(checkpoint) as (row, op):
            row, op = self._bound(row, op, reservation)
            if intent.resource not in self.accepted.plan.resources:
                raise AppApplyJournalError("UNREVIEWED_APPLY_RESOURCE")
            ledger = ledger_from_payload(row.ledger)
            prior = next((r for r in ledger.resources if r.resource.path == intent.resource.path), None)
            if (prior is None) != (intent.method == "POST") or prior and prior.uid != intent.previous_uid:
                raise AppApplyJournalError("ORIGINAL_APPLY_UID_CHANGED")
            pending = ledger.pending
            if len(op.observations) >= 1024:
                raise AppApplyJournalError("APPLY_ATTEMPT_HISTORY_BOUND")
            if intent.phase == ApplyPhase.UNSENT:
                if pending and pending.phase != ApplyPhase.REJECTED and pending != intent:
                    raise AppApplyJournalError("APPLY_UNRESOLVED_NO_RESEND")
            elif (
                not pending
                or pending.phase != ApplyPhase.UNSENT
                or asdict(pending) | {"phase": ApplyPhase.SENT} != asdict(intent)
            ):
                raise AppApplyJournalError("APPLY_SENT_REQUIRES_UNSENT")
            ledger = ApplyLedger(ledger.resources, intent)
            self._save(row, op, ledger, intent.phase.value)
            return SubmissionReceipt(
                str(row.guid), row.version, reservation.operation_id, intent.sha256, intent.phase
            )

    def commit_evidence(self, reservation, evidence, *, checkpoint):
        if type(evidence) is not NativeEvidence or evidence.intent.phase != ApplyPhase.SENT:
            raise AppApplyJournalError("INVALID_NATIVE_EVIDENCE")
        with self._locked(checkpoint, evidence_only=True) as (row, op):
            row, op = self._bound(row, op, reservation)
            ledger = ledger_from_payload(row.ledger)
            if ledger.pending != evidence.intent:
                raise AppApplyJournalError("EVIDENCE_INTENT_CHANGED")
            prior = next(
                (r for r in ledger.resources if r.resource.path == evidence.resource.resource.path), None
            )
            if (
                evidence.resource.resource != evidence.intent.resource
                or prior
                and prior.uid != evidence.resource.uid
            ):
                raise AppApplyJournalError("EVIDENCE_ORIGINAL_UID_CHANGED")
            if evidence.intent.previous_uid and evidence.resource.uid != evidence.intent.previous_uid:
                raise AppApplyJournalError("EVIDENCE_ORIGINAL_UID_CHANGED")
            resources = {r.resource.path: r for r in ledger.resources}
            resources[evidence.resource.resource.path] = evidence.resource
            pending = type(evidence.intent)(
                **(
                    asdict(evidence.intent)
                    | {"resource": evidence.intent.resource, "phase": ApplyPhase.EVIDENCE}
                )
            )
            self._save(row, op, ApplyLedger(tuple(resources.values()), pending), "EVIDENCE")
            return EvidenceReceipt(str(row.guid), row.version, reservation.operation_id, evidence.sha256)

    def commit_observation(self, reservation, evidence, *, checkpoint):
        if type(evidence) is not NativeEvidence or evidence.intent.phase != ApplyPhase.EVIDENCE:
            raise AppApplyJournalError("INVALID_NATIVE_OBSERVATION")
        with self._locked(checkpoint) as (row, op):
            row, op = self._bound(row, op, reservation)
            ledger = ledger_from_payload(row.ledger)
            if (
                ledger.pending != evidence.intent
                or evidence.resource.native_projection_sha256 != evidence.intent.resource.projection_sha256
            ):
                raise AppApplyJournalError("OBSERVATION_INTENT_CHANGED")
            prior = next(
                (r for r in ledger.resources if r.resource.path == evidence.resource.resource.path), None
            )
            if prior is None or (prior.uid, prior.native_assignments_sha256) != (
                evidence.resource.uid,
                evidence.resource.native_assignments_sha256,
            ):
                raise AppApplyJournalError("OBSERVATION_ORIGINAL_UID_CHANGED")
            resources = {r.resource.path: r for r in ledger.resources}
            resources[evidence.resource.resource.path] = evidence.resource
            op.observations[evidence.intent.submission_id] = evidence.sha256
            self._save(row, op, ApplyLedger(tuple(resources.values())), "RESERVED")
            return EvidenceReceipt(str(row.guid), row.version, reservation.operation_id, evidence.sha256)

    def commit_rejection(self, reservation, rejection, *, checkpoint):
        if type(rejection) is not NativeRejection:
            raise AppApplyJournalError("INVALID_NATIVE_REJECTION")
        with self._locked(checkpoint, evidence_only=True) as (row, op):
            row, op = self._bound(row, op, reservation)
            ledger = ledger_from_payload(row.ledger)
            if ledger.pending != rejection.intent:
                raise AppApplyJournalError("REJECTION_INTENT_CHANGED")
            pending = type(rejection.intent)(
                **(
                    asdict(rejection.intent)
                    | {"resource": rejection.intent.resource, "phase": ApplyPhase.REJECTED}
                )
            )
            op.observations[rejection.intent.submission_id] = {
                "state": "REJECTED",
                "intent": _json(asdict(rejection.intent)),
                "http_code": rejection.http_code,
                "reason": rejection.reason,
                "response_sha256": rejection.response_sha256,
            }
            self._save(row, op, ApplyLedger(ledger.resources, pending, rejection), "REJECTED")
            return EvidenceReceipt(str(row.guid), row.version, reservation.operation_id, rejection.sha256)

    def record_result(self, reservation, receipt, *, checkpoint):
        if (
            type(receipt) is not AppApplyReceipt
            or not receipt.configuration_observed
            or receipt.workload_ready
            or receipt.plan_sha256 != self.accepted.plan.sha256
            or receipt.ledger.pending
            or receipt.ledger.rejection
        ):
            raise AppApplyJournalError("APPLY_INCOMPLETE_CONFIGURATION")
        with self._locked(checkpoint) as (row, op):
            row, op = self._bound(row, op, reservation)
            committed = ledger_from_payload(row.ledger)
            if committed.pending or committed.rejection:
                raise AppApplyJournalError("APPLY_COMMITTED_ATTEMPT_UNRESOLVED")
            recorded = {r.resource.path: r for r in committed.resources}
            observed = {r.resource.path: r for r in receipt.ledger.resources}
            if set(observed) != {r.path for r in self.accepted.plan.resources} or set(recorded) != set(
                observed
            ):
                raise AppApplyJournalError("ANCILLARY_CONFIGURATION_INCOMPLETE")
            for path, actual in observed.items():
                prior = recorded[path]
                if (
                    actual.resource not in self.accepted.plan.resources
                    or actual.native_projection_sha256 != actual.resource.projection_sha256
                    or prior.resource != actual.resource
                    or prior.native_projection_sha256 != actual.native_projection_sha256
                    or prior.native_template_sha256 != actual.native_template_sha256
                    or prior.native_placement_sha256 != actual.native_placement_sha256
                    or (actual.uid, actual.native_assignments_sha256)
                    != (prior.uid, prior.native_assignments_sha256)
                ):
                    raise AppApplyJournalError("APPLY_OBSERVATION_CHANGED")
            row.observed_at = op.observed_at = timezone.now()
            self._save(row, op, receipt.ledger, "OBSERVED")

    def runtime_handoff(self, reservation, *, checkpoint):
        """Read actual current completed records; metadata never substitutes admission."""
        with self._locked(checkpoint) as (row, op):
            row, op = self._bound(row, op, reservation)
            ledger = ledger_from_payload(row.ledger)
            if (
                row.state != "OBSERVED"
                or op.state != "OBSERVED"
                or row.observed_at is None
                or op.observed_at is None
                or ledger.pending
                or ledger.rejection
                or self.accepted.plan.placement is None
                or {r.resource.path for r in ledger.resources}
                != {r.path for r in self.accepted.plan.resources}
                or any(
                    r.resource not in self.accepted.plan.resources
                    or r.native_projection_sha256 != r.resource.projection_sha256
                    for r in ledger.resources
                )
            ):
                raise AppApplyJournalError("RUNTIME_APPLY_NOT_COMPLETED")
            return RuntimeHandoff(
                str(row.guid),
                row.version,
                str(op.operation_id),
                op.generation,
                self.accepted.plan,
                self.accepted.prepared.service_account_name,
                tuple(
                    r
                    for r in ledger.resources
                    if r.resource.kind in ("Deployment", "StatefulSet", "DaemonSet")
                ),
            )

    def validate_runtime_handoff(self, reservation, handoff, *, checkpoint):
        if type(handoff) is not RuntimeHandoff or handoff != self.runtime_handoff(
            reservation, checkpoint=checkpoint
        ):
            raise AppApplyJournalError("CURRENT_RUNTIME_HANDOFF_CHANGED")


@contextmanager
def app_apply_mutex(accepted, *, preparation, reservation, iam, preparation_checkpoint, iam_checkpoint):
    _outside_atomic()
    if type(accepted) is not AcceptedApply or connection.vendor != "postgresql":
        raise AppApplyJournalError("POSTGRES_APPLY_MUTEX_REQUIRED")
    identity = (
        "astrolift-gcp-app-apply-v1",
        accepted.prepared.app_guid,
        accepted.prepared.cluster_guid,
        accepted.prepared.namespace,
    )
    lock_id = int.from_bytes(hashlib.sha256(json.dumps(identity).encode()).digest()[:8], "big", signed=True)
    mutex = connection.Database.connect(**connection.get_connection_params())
    mutex.autocommit = True
    store = AppApplyStore(
        accepted,
        mutex,
        lock_id,
        preparation=preparation,
        reservation=reservation,
        iam=iam,
        preparation_checkpoint=preparation_checkpoint,
        iam_checkpoint=iam_checkpoint,
    )
    try:
        with mutex.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            if not cursor.fetchone()[0]:
                raise AppApplyJournalError("APPLY_BUSY")
        yield store
    finally:
        store._active = False
        try:
            with mutex.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
        finally:
            mutex.close()
