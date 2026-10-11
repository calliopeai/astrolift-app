"""Committed GKE preparation metadata, separate from native transport and authority.

The caller holds both nonblocking mutexes when IAM evidence is required. No
method permits an enclosing transaction or treats absent workers as cancellation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from enum import StrEnum
from typing import Any
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from gcp.gke_identity_observation import GKEObservationContext
from gcp.gke_identity_preparation import (
    GKEPreparationLedger,
    GKEPreparationReceipt,
    ObjectObservation,
    PreparationCommitReceipt,
    PreparationPhase,
    PreparationSubmission,
    VerifiedIAMConfigurationReceipt,
    preparation_target_sha256,
)
from gcp.gke_preparation_codec import preparation_ledger_from_payload, preparation_ledger_payload
from gcp.identity_owned import NativeGCPIdentity

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.gcp_workload_identity_journal import (
    JournalCheckpoint as IAMCheckpoint,
)
from astrolift_services.gcp_workload_identity_journal import (
    JournalError,
    KSAIdentity,
    _digest,
    _hash,
    _outside_atomic,
    _uuid,
)
from astrolift_services.gcp_workload_identity_journal import (
    JournalStore as IAMStore,
)
from astrolift_services.gcp_workload_identity_journal import (
    JournalTarget as IAMTarget,
)
from astrolift_services.gcp_workload_identity_journal import (
    Reservation as IAMReservation,
)
from astrolift_services.models import (
    GCPGKEPreparationJournal,
    GCPGKEPreparationOperation,
    GCPWorkloadIdentityJournal,
)
from astrolift_workflows.gcp_identity_inputs import (
    AcceptedPreparationTemplate,
    LogicalSubject,
    accepted_preparation_template_from_payload,
)
from astrolift_workflows.native_identity_inputs import AcceptedAppIdentityAuthority


class PreparationJournalError(JournalError):
    """Only fixed refusal reasons may leave the private adapter."""


@contextmanager
def _short_transaction() -> Iterator[None]:
    try:
        with transaction.atomic():
            yield
    except DatabaseError as error:
        cause = error.__cause__
        if getattr(cause, "pgcode", None) == "55P03" or getattr(cause, "sqlstate", None) == "55P03":
            raise PreparationJournalError("JOURNAL_BUSY") from None
        raise PreparationJournalError("JOURNAL_DATABASE_UNCONFIRMED") from None


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def authority_reference_payload(reference: AcceptedAppIdentityAuthority) -> dict:
    """Shape/integrity only; the current checkpoint must verify original authority."""
    if type(reference) is not AcceptedAppIdentityAuthority:
        raise PreparationJournalError("INVALID_AUTHORITY_REFERENCE")
    payload = _json(asdict(reference))
    if authority_reference_from_payload(payload) != reference:
        raise PreparationJournalError("INVALID_AUTHORITY_REFERENCE")
    return payload


def authority_reference_from_payload(payload: object) -> AcceptedAppIdentityAuthority:
    try:
        if type(payload) is not dict or set(payload) != {
            field.name for field in fields(AcceptedAppIdentityAuthority)
        }:
            raise ValueError
        row = dict(payload)
        if type(row["accepted_token_scopes"]) is not list or len(row["accepted_token_scopes"]) > 64:
            raise ValueError
        if any(
            type(scope) is not str or not re.fullmatch(r"[A-Za-z0-9_.:*\-]{1,64}", scope)
            for scope in row["accepted_token_scopes"]
        ):
            raise ValueError
        row["accepted_token_scopes"] = tuple(row["accepted_token_scopes"])
        reference = AcceptedAppIdentityAuthority(**row)
        if (
            type(reference.schema) is not int
            or reference.schema != 1
            or type(reference.actor_user_id) is not int
            or reference.actor_user_id < 1
        ):
            raise ValueError
        for name in (
            "organization_guid",
            "environment_guid",
            "app_guid",
            "cluster_guid",
            "provider_guid",
            "credential_guid",
        ):
            _uuid(getattr(reference, name))
        for name in ("team_guid", "project_guid", "token_team_guid"):
            if getattr(reference, name) is not None:
                _uuid(getattr(reference, name))
        _digest(reference.signature)
        _digest(reference.credential_binding)
        if reference.permission not in ("app.update", "app.deploy") or reference.credential_kind not in (
            "api_token",
            "browser_session",
        ):
            raise ValueError
        if reference.credential_kind == "browser_session" and (
            reference.accepted_token_scopes or reference.token_team_guid is not None
        ):
            raise ValueError
        return reference
    except (TypeError, ValueError, KeyError, AttributeError):
        raise PreparationJournalError("INVALID_AUTHORITY_REFERENCE") from None


@dataclass(frozen=True)
class PreparationTarget:
    context: GKEObservationContext
    provider_id: str
    credential_declaration_sha256: str
    original_subjects: tuple[LogicalSubject, ...]

    def __post_init__(self) -> None:
        if type(self.context) is not GKEObservationContext or type(self.original_subjects) is not tuple:
            raise PreparationJournalError("INVALID_PREPARATION_TARGET")
        _uuid(self.provider_id)
        _digest(self.credential_declaration_sha256)
        # A logical template validates the original alias association, without a plan.
        AcceptedPreparationTemplate((), self.original_subjects, "0" * 64)
        native = {(subject.namespace, subject.name) for subject in self.context.subjects}
        if native != {(subject.namespace, subject.name) for subject in self.original_subjects}:
            raise PreparationJournalError("ORIGINAL_SUBJECT_SET_CHANGED")

    @property
    def payload(self) -> dict:
        native = self.context.identity
        return {
            "schema_version": 1,
            "organization_id": native.organization_id,
            "app_id": native.app_id,
            "cluster_id": native.cluster_id,
            "provider_id": self.provider_id,
            "project_id": native.project_id,
            "project_number": native.project_number,
            "region": native.region,
            "gsa_id": native.service_account_id,
            "gsa_unique_id": native.service_account_unique_id,
            "gke_location": self.context.location,
            "gke_name": self.context.cluster_name,
            "gke_cluster_id": self.context.native_cluster_id,
            "credential_declaration_sha256": self.credential_declaration_sha256,
            "context_sha256": native.fingerprint,
            "preparation_sha256": preparation_target_sha256(self.context),
            "subjects": [asdict(subject) for subject in self.context.subjects],
            "original_aliases": AcceptedPreparationTemplate((), self.original_subjects, "0" * 64).payload[
                "subjects"
            ],
        }

    @property
    def sha256(self) -> str:
        return _hash(self.payload)


@dataclass(frozen=True)
class PreparationOperation:
    operation_id: str
    workflow_id: str
    execution_id: str
    desired_revision: int
    template: AcceptedPreparationTemplate
    authority: AcceptedAppIdentityAuthority

    def __post_init__(self) -> None:
        _uuid(self.operation_id)
        if type(self.template) is not AcceptedPreparationTemplate:
            raise PreparationJournalError("INVALID_OPERATION_TEMPLATE")
        authority_reference_payload(self.authority)
        if type(self.desired_revision) is not int or not 1 <= self.desired_revision <= 2**63 - 1:
            raise PreparationJournalError("INVALID_REVISION")
        if any(
            type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value)
            for value in (self.workflow_id, self.execution_id)
        ):
            raise PreparationJournalError("INVALID_OPERATION")

    @property
    def authority_reference_sha256(self) -> str:
        return _hash(authority_reference_payload(self.authority))


@dataclass(frozen=True)
class PreparationReservation:
    journal_id: str
    operation_id: str
    generation: int
    nonce: str
    target_sha256: str
    accepted_union_template_sha256: str
    authority_reference_sha256: str

    def __post_init__(self) -> None:
        for value in (self.journal_id, self.operation_id, self.nonce):
            _uuid(value)
        for value in (
            self.target_sha256,
            self.accepted_union_template_sha256,
            self.authority_reference_sha256,
        ):
            _digest(value)
        if type(self.generation) is not int or self.generation < 1:
            raise PreparationJournalError("INVALID_RESERVATION")


class PreparationStage(StrEnum):
    PREPARE = "PREPARE"
    ANNOTATE = "ANNOTATE"


@dataclass(frozen=True)
class PreparationCheckpoint:
    organization: Organization
    team: Team
    project: Project | None
    app: RegisteredApp
    cluster: TenantCluster
    provider: ProviderPlugin
    environments: tuple[AppEnvironment, ...]
    journal: GCPGKEPreparationJournal | None
    accepted: GCPGKEPreparationOperation | None
    iam_journal: GCPWorkloadIdentityJournal | None
    target: PreparationTarget
    operation: PreparationOperation


Checkpoint = Callable[[PreparationCheckpoint], None]
IAMBinding = tuple[IAMStore, IAMReservation]


class PreparationStore:
    def __init__(self, target: PreparationTarget, mutex: Any, lock_id: int):
        self.target, self._mutex, self._lock_id, self._active = target, mutex, lock_id, True

    def _ready(self) -> None:
        _outside_atomic()
        if not self._active or self._mutex.closed:
            raise PreparationJournalError("MUTEX_NOT_HELD")
        with self._mutex.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype=%s AND pid=pg_backend_pid() AND classid=%s AND objid=%s AND objsubid=1 AND granted)",
                ["advisory", (self._lock_id >> 32) & 0xFFFFFFFF, self._lock_id & 0xFFFFFFFF],
            )
            if not cursor.fetchone()[0]:
                raise PreparationJournalError("MUTEX_NOT_HELD")

    def _validate_operation(self, operation: PreparationOperation) -> None:
        target, native = self.target, self.target.context.identity
        reference = operation.authority
        if (
            reference.organization_guid,
            reference.app_guid,
            reference.cluster_guid,
            reference.provider_guid,
        ) != (native.organization_id, native.app_id, native.cluster_id, target.provider_id):
            raise PreparationJournalError("AUTHORITY_TARGET_CHANGED")
        if reference.environment_guid not in {
            guid for subject in operation.template.subjects for guid in subject.environment_ids
        }:
            raise PreparationJournalError("AUTHORITY_ENVIRONMENT_CHANGED")
        if {(subject.namespace, subject.name) for subject in operation.template.subjects} != {
            (subject.namespace, subject.name) for subject in target.original_subjects
        }:
            raise PreparationJournalError("PHYSICAL_SUBJECT_EVOLUTION_REQUIRES_REVIEW")
        with NativeGCPIdentity(native) as validator:
            for grant in operation.template.permissions:
                if validator._resource(grant.resource) != grant.resource or not validator._role(grant.role):
                    raise PreparationJournalError("FOREIGN_ACCEPTED_GRANT")

    @contextmanager
    def _locked(
        self, operation: PreparationOperation, checkpoint: Checkpoint, *, iam: IAMBinding | None = None
    ) -> Iterator[
        tuple[
            GCPGKEPreparationJournal | None,
            GCPGKEPreparationOperation | None,
            GCPWorkloadIdentityJournal | None,
        ]
    ]:
        self._ready()
        if type(operation) is not PreparationOperation or not callable(checkpoint):
            raise PreparationJournalError("CURRENT_AUTHORITY_REQUIRED")
        self._validate_operation(operation)
        if iam is not None:
            if (
                type(iam) is not tuple
                or len(iam) != 2
                or type(iam[0]) is not IAMStore
                or type(iam[1]) is not IAMReservation
            ):
                raise PreparationJournalError("CURRENT_IAM_MUTEX_REQUIRED")
            iam[0]._ready()
        native = self.target.context.identity
        with _short_transaction():
            try:
                org = Organization._unscoped.select_for_update(nowait=True).get(
                    guid=native.organization_id, deleted_at__isnull=True
                )
                cluster = TenantCluster._unscoped.select_for_update(nowait=True).get(
                    guid=native.cluster_id, deleted_at__isnull=True
                )
                provider = ProviderPlugin._unscoped.select_for_update(nowait=True).get(
                    guid=self.target.provider_id, deleted_at__isnull=True
                )
                observed = RegisteredApp._unscoped.get(guid=native.app_id)
                team = Team._unscoped.select_for_update(nowait=True).get(
                    pk=observed.team_id, organization=org, deleted_at__isnull=True
                )
                project = None
                if observed.project_id:
                    project = Project._unscoped.select_for_update(nowait=True).get(
                        pk=observed.project_id, organization=org, team=team, deleted_at__isnull=True
                    )
                app = RegisteredApp._unscoped.select_for_update(nowait=True).get(
                    guid=native.app_id,
                    organization=org,
                    team=team,
                    project_id=observed.project_id,
                    deleted_at__isnull=True,
                )
            except (
                Organization.DoesNotExist,
                TenantCluster.DoesNotExist,
                ProviderPlugin.DoesNotExist,
                RegisteredApp.DoesNotExist,
                Team.DoesNotExist,
                Project.DoesNotExist,
            ):
                raise PreparationJournalError("CURRENT_OWNER_UNAVAILABLE") from None
            if (
                cluster.organization_id not in (None, org.pk)
                or cluster.provider_plugin_id != provider.pk
                or provider.slug != "gcp"
                or not cluster.is_active
                or not provider.is_enabled
            ):
                raise PreparationJournalError("CURRENT_TARGET_CHANGED")
            aliases = sorted(
                guid for subject in operation.template.subjects for guid in subject.environment_ids
            )
            environments = tuple(
                AppEnvironment._unscoped.filter(
                    guid__in=aliases, registered_app=app, tenant_cluster=cluster, deleted_at__isnull=True
                ).order_by("guid")
            )
            if {str(environment.guid) for environment in environments} != set(aliases):
                raise PreparationJournalError("CURRENT_ENVIRONMENT_UNAVAILABLE")
            # IAM always precedes preparation if both journals are locked.
            iam_row = None
            if iam is not None:
                iam_row = (
                    GCPWorkloadIdentityJournal._unscoped.select_for_update(nowait=True)
                    .filter(registered_app=app, tenant_cluster=cluster, guid=iam[1].journal_id)
                    .first()
                )
                if (
                    iam_row is None
                    or iam_row.deleted_at is not None
                    or iam_row.organization_id != org.pk
                    or iam_row.provider_plugin_id != provider.pk
                ):
                    raise PreparationJournalError("CURRENT_IAM_UNAVAILABLE")
                iam[0]._bound(iam_row, iam[1])
            rows = GCPGKEPreparationJournal._unscoped.select_for_update(nowait=True).filter(
                registered_app=app, tenant_cluster=cluster
            )
            if rows.filter(deleted_at__isnull=False).exists():
                raise PreparationJournalError("RETIRED_JOURNAL_REQUIRES_REVIEW")
            journal = rows.first()
            if journal and (
                journal.organization_id != org.pk
                or journal.provider_plugin_id != provider.pk
                or journal.target_sha256 != self.target.sha256
                or journal.target_snapshot != _json(self.target.payload)
            ):
                raise PreparationJournalError("ORIGINAL_TARGET_CHANGED")
            accepted = None
            if journal:
                histories = GCPGKEPreparationOperation._unscoped.select_for_update(nowait=True).filter(
                    journal=journal
                )
                if histories.filter(deleted_at__isnull=False).exists():
                    raise PreparationJournalError("RETIRED_OPERATION_REQUIRES_REVIEW")
                accepted = histories.filter(
                    operation_id=journal.operation_id, generation=journal.generation
                ).first()
                if accepted is None:
                    raise PreparationJournalError("OPERATION_INTEGRITY_FAILURE")
                self._accepted_operation(accepted, journal)
            context = PreparationCheckpoint(
                org,
                team,
                project,
                app,
                cluster,
                provider,
                environments,
                journal,
                accepted,
                iam_row,
                self.target,
                operation,
            )
            if checkpoint(context) is not None:
                raise PreparationJournalError("CURRENT_AUTHORITY_UNCONFIRMED")
            self._parents = (org, app, cluster, provider)
            self._checkpoint_context = context
            yield journal, accepted, iam_row

    def _accepted_operation(
        self, accepted: GCPGKEPreparationOperation, journal: GCPGKEPreparationJournal
    ) -> PreparationOperation:
        try:
            operation = PreparationOperation(
                str(accepted.operation_id),
                accepted.workflow_id,
                accepted.execution_id,
                accepted.desired_revision,
                accepted_preparation_template_from_payload(accepted.accepted_union_template),
                authority_reference_from_payload(accepted.accepted_authority_reference),
            )
        except ValueError:
            raise PreparationJournalError("OPERATION_INTEGRITY_FAILURE") from None
        if (
            accepted.organization_id,
            accepted.journal_id,
            accepted.generation,
            str(accepted.reservation_nonce),
            accepted.accepted_union_template_sha256,
            accepted.authority_reference_sha256,
            accepted.derived_native_union_sha256,
            accepted.state,
            accepted.ledger_sha256,
            accepted.submissions,
            accepted.ledger,
        ) != (
            journal.organization_id,
            journal.pk,
            journal.generation,
            str(journal.reservation_nonce),
            operation.template.sha256,
            operation.authority_reference_sha256,
            journal.derived_native_union_sha256,
            journal.state,
            journal.ledger_sha256,
            journal.submissions,
            journal.ledger,
        ):
            raise PreparationJournalError("OPERATION_INTEGRITY_FAILURE")
        if (
            journal.workflow_id,
            journal.execution_id,
            journal.desired_revision,
            journal.accepted_union_template_sha256,
            journal.authority_reference_sha256,
        ) != (
            operation.workflow_id,
            operation.execution_id,
            operation.desired_revision,
            operation.template.sha256,
            operation.authority_reference_sha256,
        ):
            raise PreparationJournalError("OPERATION_INTEGRITY_FAILURE")
        return operation

    def _operation(self, reservation: PreparationReservation) -> PreparationOperation:
        self._ready()
        row = GCPGKEPreparationJournal._unscoped.filter(guid=reservation.journal_id).first()
        if row is None:
            raise PreparationJournalError("STALE_RESERVATION")
        accepted = GCPGKEPreparationOperation._unscoped.filter(
            journal=row, operation_id=reservation.operation_id, deleted_at__isnull=True
        ).first()
        if accepted is None:
            raise PreparationJournalError("STALE_RESERVATION")
        return self._accepted_operation(accepted, row)

    @staticmethod
    def _reservation(row: GCPGKEPreparationJournal) -> PreparationReservation:
        return PreparationReservation(
            str(row.guid),
            str(row.operation_id),
            row.generation,
            str(row.reservation_nonce),
            row.target_sha256,
            row.accepted_union_template_sha256,
            row.authority_reference_sha256,
        )

    def _bound(
        self, row: GCPGKEPreparationJournal | None, reservation: PreparationReservation
    ) -> GCPGKEPreparationJournal:
        if (
            type(reservation) is not PreparationReservation
            or row is None
            or self._reservation(row) != reservation
        ):
            raise PreparationJournalError("STALE_RESERVATION")
        return row

    def _ledger(self, row: GCPGKEPreparationJournal) -> GKEPreparationLedger:
        try:
            ledger = preparation_ledger_from_payload(row.ledger, context=self.target.context)
            if ledger.sha256 != row.ledger_sha256 or ledger.target_sha256 != row.preparation_sha256:
                raise ValueError
            return ledger
        except ValueError:
            raise PreparationJournalError("JOURNAL_INTEGRITY_FAILURE") from None

    def _validated(self, ledger: GKEPreparationLedger) -> dict:
        try:
            payload = preparation_ledger_payload(ledger)
            if preparation_ledger_from_payload(payload, context=self.target.context) != ledger:
                raise ValueError
            return payload
        except (ValueError, TypeError, AttributeError):
            raise PreparationJournalError("INVALID_PREPARATION_LEDGER") from None

    def _save(self, row: GCPGKEPreparationJournal, accepted: GCPGKEPreparationOperation) -> None:
        row.save()
        for name in (
            "state",
            "submissions",
            "ledger",
            "ledger_sha256",
            "derived_native_union_sha256",
            "prepared_at",
            "observed_at",
        ):
            setattr(accepted, name, getattr(row, name))
        accepted.save()

    def reserve(
        self,
        operation: PreparationOperation,
        *,
        checkpoint: Checkpoint,
        expected_version: int | None = None,
        previous_iam: IAMBinding | None = None,
    ) -> PreparationReservation:
        with self._locked(operation, checkpoint, iam=previous_iam) as (row, accepted, iam_row):
            if row and str(row.operation_id) == operation.operation_id:
                if self._accepted_operation(accepted, row) != operation:
                    raise PreparationJournalError("OPERATION_IDENTITY_CHANGED")
                return self._reservation(row)
            if row is not None:
                old_ledger = self._ledger(row)
                if row.state != row.State.OBSERVED or old_ledger.pending or accepted.observed_at is None:
                    raise PreparationJournalError("UNRESOLVED_OPERATION")
                if expected_version != row.version or operation.desired_revision <= row.desired_revision:
                    raise PreparationJournalError("STALE_REVISION")
                if previous_iam is None or iam_row is None:
                    raise PreparationJournalError("PREVIOUS_IAM_COMPLETION_REQUIRED")
                self._verify_iam(row, accepted, previous_iam, iam_row, previous=True)
                row.generation += 1
                row.submissions = {}
                row.derived_native_union_sha256 = ""
                row.prepared_at = None
                row.observed_at = None
            else:
                if expected_version is not None or previous_iam is not None:
                    raise PreparationJournalError("STALE_REVISION")
                org, app, cluster, provider = self._parents
                ledger = GKEPreparationLedger(preparation_target_sha256(self.target.context))
                row = GCPGKEPreparationJournal(
                    organization=org,
                    registered_app=app,
                    tenant_cluster=cluster,
                    provider_plugin=provider,
                    target_snapshot=self.target.payload,
                    target_sha256=self.target.sha256,
                    context_sha256=self.target.context.identity.fingerprint,
                    preparation_sha256=ledger.target_sha256,
                    ledger=self._validated(ledger),
                    ledger_sha256=ledger.sha256,
                )
            row.operation_id = operation.operation_id
            row.reservation_nonce = uuid4()
            row.workflow_id, row.execution_id = operation.workflow_id, operation.execution_id
            row.desired_revision = operation.desired_revision
            row.accepted_union_template_sha256 = operation.template.sha256
            row.authority_reference_sha256 = operation.authority_reference_sha256
            row.state = row.State.RESERVED
            row.save()
            accepted = GCPGKEPreparationOperation(
                organization=row.organization,
                journal=row,
                operation_id=operation.operation_id,
                reservation_nonce=row.reservation_nonce,
                generation=row.generation,
                desired_revision=row.desired_revision,
                accepted_union_template=operation.template.payload,
                accepted_union_template_sha256=operation.template.sha256,
                accepted_authority_reference=authority_reference_payload(operation.authority),
                authority_reference_sha256=operation.authority_reference_sha256,
                workflow_id=operation.workflow_id,
                execution_id=operation.execution_id,
                state=row.state,
                ledger=row.ledger,
                ledger_sha256=row.ledger_sha256,
            )
            accepted.save()
            return self._reservation(row)

    def validate_current(
        self, reservation: PreparationReservation, *, checkpoint: Checkpoint, iam: IAMBinding | None = None
    ) -> None:
        with self._locked(self._operation(reservation), checkpoint, iam=iam) as (row, accepted, iam_row):
            row = self._bound(row, reservation)
            if iam is not None:
                self._annotation_current(row, accepted, iam, iam_row)

    def read(self, reservation: PreparationReservation, *, checkpoint: Checkpoint) -> GKEPreparationLedger:
        with self._locked(self._operation(reservation), checkpoint) as (row, _, _):
            return self._ledger(self._bound(row, reservation))

    def _complete_objects(self, ledger: GKEPreparationLedger) -> bool:
        expected = {
            path
            for subject in self.target.context.subjects
            for path in (
                f"/api/v1/namespaces/{subject.namespace}",
                f"/api/v1/namespaces/{subject.namespace}/serviceaccounts/{subject.name}",
            )
        }
        return not ledger.pending and {item.path for item in ledger.objects} == expected

    def iam_target(self, reservation: PreparationReservation, *, checkpoint: Checkpoint) -> IAMTarget:
        with self._locked(self._operation(reservation), checkpoint) as (row, _, _):
            row = self._bound(row, reservation)
            if row.state not in (row.State.PREPARED, row.State.OBSERVED) or not self._complete_objects(
                self._ledger(row)
            ):
                raise PreparationJournalError("PREPARATION_NOT_COMPLETED")
            return self._iam_target(self._ledger(row))

    def _iam_target(self, ledger: GKEPreparationLedger) -> IAMTarget:
        objects = {item.path: item for item in ledger.objects}
        identities = tuple(
            KSAIdentity(
                subject.environment_ids[0],
                subject.namespace,
                subject.name,
                objects[f"/api/v1/namespaces/{subject.namespace}"].uid,
                objects[f"/api/v1/namespaces/{subject.namespace}/serviceaccounts/{subject.name}"].uid,
            )
            for subject in self.target.original_subjects
        )
        native, context = self.target.context.identity, self.target.context
        return IAMTarget(
            native.organization_id,
            native.app_id,
            native.cluster_id,
            self.target.provider_id,
            native.project_id,
            native.project_number,
            native.region,
            native.service_account_id,
            native.service_account_unique_id,
            context.location,
            context.cluster_name,
            context.native_cluster_id,
            self.target.credential_declaration_sha256,
            native.fingerprint,
            identities,
        )

    def bind_native_union(self, reservation: PreparationReservation, *, checkpoint: Checkpoint) -> str:
        with self._locked(self._operation(reservation), checkpoint) as (row, accepted, _):
            row = self._bound(row, reservation)
            ledger = self._ledger(row)
            if row.state != row.State.PREPARED or not self._complete_objects(ledger):
                raise PreparationJournalError("PREPARATION_NOT_COMPLETED")
            operation = self._accepted_operation(accepted, row)
            uids = tuple(identity.service_account_uid for identity in self._iam_target(ledger).ksa_identities)
            # The same pure planner as reconcile; close the port even when a
            # future native constructor owns private tracing/logging contexts.
            with NativeGCPIdentity(self.target.context.identity) as validator:
                derived = validator._desired(
                    tuple(asdict(grant) for grant in operation.template.permissions),
                    service_account_uids=uids,
                )[2]
            if row.derived_native_union_sha256 and row.derived_native_union_sha256 != derived:
                raise PreparationJournalError("NATIVE_UNION_ALREADY_BOUND")
            if not row.derived_native_union_sha256:
                row.derived_native_union_sha256 = derived
                self._save(row, accepted)
            return derived

    def _verify_iam(
        self,
        row: GCPGKEPreparationJournal,
        accepted: GCPGKEPreparationOperation,
        binding: IAMBinding,
        iam_row: GCPWorkloadIdentityJournal,
        *,
        previous: bool = False,
    ) -> None:
        store, reservation = binding
        ledger = self._ledger(row)
        if (
            not self._complete_objects(replace(ledger, pending=()))
            or any(intent.method != "PATCH" for intent in ledger.pending)
            or not row.derived_native_union_sha256
            or store.target != self._iam_target(ledger)
        ):
            raise PreparationJournalError("IAM_ORIGINAL_TARGET_CHANGED")
        if (
            iam_row.target_sha256 != store.target.sha256
            or iam_row.target_snapshot != _json(store.target.payload)
            or iam_row.context_sha256 != self.target.context.identity.fingerprint
        ):
            raise PreparationJournalError("IAM_ORIGINAL_TARGET_CHANGED")
        store._bound(iam_row, reservation)
        iam_ledger = store._ledger(iam_row)
        if (
            iam_row.state != iam_row.State.OBSERVED
            or iam_row.observed_revision != iam_row.desired_revision
            or iam_row.observed_at is None
            or iam_ledger.pending
            or iam_ledger.removals
            or type(iam_row.submissions) is not dict
            or len(iam_row.submissions) > 65
            or any(
                type(step) is not dict or step.get("state") != "OBSERVED"
                for step in iam_row.submissions.values()
            )
        ):
            raise PreparationJournalError("IAM_CONFIGURATION_NOT_COMPLETED")
        if (
            str(iam_row.operation_id),
            iam_row.workflow_id,
            iam_row.execution_id,
            iam_row.desired_revision,
            iam_row.desired_union_sha256,
            iam_row.authority_reference_sha256,
        ) != (
            str(accepted.operation_id),
            accepted.workflow_id,
            accepted.execution_id,
            accepted.desired_revision,
            row.derived_native_union_sha256,
            accepted.authority_reference_sha256,
        ):
            raise PreparationJournalError("IAM_OPERATION_CHANGED")
        if previous and (
            str(accepted.completed_iam_journal_id),
            accepted.completed_iam_journal_version,
            accepted.completed_iam_generation,
            accepted.completed_iam_ledger_sha256,
        ) != (str(iam_row.guid), iam_row.version, iam_row.generation, iam_row.ledger_sha256):
            raise PreparationJournalError("PREVIOUS_IAM_FENCE_CHANGED")

    def verified_annotation_receipt(
        self,
        reservation: PreparationReservation,
        *,
        iam: IAMBinding,
        iam_checkpoint: Callable[[IAMCheckpoint], None],
        checkpoint: Checkpoint,
    ) -> VerifiedIAMConfigurationReceipt:
        self._ready()
        if (
            type(iam) is not tuple
            or len(iam) != 2
            or type(iam[0]) is not IAMStore
            or type(iam[1]) is not IAMReservation
            or not callable(iam_checkpoint)
        ):
            raise PreparationJournalError("CURRENT_IAM_MUTEX_REQUIRED")
        # Fresh actual committed completion, followed by joint IAM-first row locks.
        iam[0].completed_configuration(iam[1], checkpoint=iam_checkpoint)
        with self._locked(self._operation(reservation), checkpoint, iam=iam) as (row, accepted, iam_row):
            row = self._bound(row, reservation)
            self._verify_iam(row, accepted, iam, iam_row)
            context = self._checkpoint_context
            from astrolift_services.gcp_workload_identity_journal import OperationIdentity as IAMOperation

            iam_operation = IAMOperation(
                str(iam_row.operation_id),
                iam_row.workflow_id,
                iam_row.execution_id,
                iam_row.desired_revision,
                iam_row.desired_union_sha256,
                iam_row.authority_reference_sha256,
            )
            if (
                iam_checkpoint(
                    IAMCheckpoint(
                        context.organization,
                        context.team,
                        context.project,
                        context.app,
                        context.cluster,
                        context.provider,
                        iam_row,
                        iam[0].target,
                        iam_operation,
                    )
                )
                is not None
            ):
                raise PreparationJournalError("CURRENT_IAM_AUTHORITY_UNCONFIRMED")
            accepted.completed_iam_journal_id = iam_row.guid
            accepted.completed_iam_journal_version = iam_row.version
            accepted.completed_iam_generation = iam_row.generation
            accepted.completed_iam_ledger_sha256 = iam_row.ledger_sha256
            accepted.save()
            return VerifiedIAMConfigurationReceipt(
                str(iam_row.guid),
                iam_row.version,
                str(iam_row.operation_id),
                self.target.context.identity.fingerprint,
                row.derived_native_union_sha256,
                tuple(identity.service_account_uid for identity in iam[0].target.ksa_identities),
                True,
            )

    def _annotation_current(
        self,
        row: GCPGKEPreparationJournal,
        accepted: GCPGKEPreparationOperation,
        iam: IAMBinding | None,
        iam_row: GCPWorkloadIdentityJournal | None,
    ) -> None:
        if iam is None or iam_row is None:
            raise PreparationJournalError("CURRENT_IAM_COMPLETION_REQUIRED")
        self._verify_iam(row, accepted, iam, iam_row)
        if (
            str(accepted.completed_iam_journal_id),
            accepted.completed_iam_journal_version,
            accepted.completed_iam_generation,
            accepted.completed_iam_ledger_sha256,
        ) != (str(iam_row.guid), iam_row.version, iam_row.generation, iam_row.ledger_sha256):
            raise PreparationJournalError("ANNOTATION_IAM_FENCE_CHANGED")

    def commit_submission(
        self,
        reservation: PreparationReservation,
        submission: PreparationSubmission,
        *,
        checkpoint: Checkpoint,
        iam: IAMBinding | None = None,
    ) -> PreparationCommitReceipt:
        if type(submission) is not PreparationSubmission:
            raise PreparationJournalError("INVALID_SUBMISSION")
        payload = self._validated(submission.ledger)
        intent = submission.intent
        if (
            intent.operation_id != reservation.operation_id
            or intent not in submission.ledger.pending
            or submission.target_sha256 != submission.ledger.target_sha256
        ):
            raise PreparationJournalError("SUBMISSION_IDENTITY_CHANGED")
        with self._locked(self._operation(reservation), checkpoint, iam=iam) as (row, accepted, iam_row):
            row = self._bound(row, reservation)
            old = self._ledger(row)
            if row.state == row.State.OBSERVED:
                raise PreparationJournalError("TERMINAL_OPERATION")
            prior = next((item for item in old.pending if item.path == intent.path), None)
            if old.objects != submission.ledger.objects or tuple(
                item for item in old.pending if item.path != intent.path
            ) != tuple(item for item in submission.ledger.pending if item.path != intent.path):
                raise PreparationJournalError("SUBMISSION_LEDGER_CHANGED")
            if (
                any(item.phase == PreparationPhase.SENT for item in old.pending)
                or row.state == row.State.UNKNOWN
            ):
                raise PreparationJournalError("SENT_SUBMISSION_UNRESOLVED")
            if intent.method == "PATCH":
                self._annotation_current(row, accepted, iam, iam_row)
            elif row.derived_native_union_sha256 or row.state in (row.State.PREPARED, row.State.OBSERVED):
                raise PreparationJournalError("PREPARATION_ALREADY_COMPLETED")
            if intent.phase == PreparationPhase.SENT:
                if (
                    prior is None
                    or prior.phase != PreparationPhase.UNSENT
                    or replace(intent, phase=PreparationPhase.UNSENT) != prior
                ):
                    raise PreparationJournalError("UNSENT_RESERVATION_REQUIRED")
            elif intent.phase == PreparationPhase.UNSENT:
                if prior is not None and prior != intent:
                    raise PreparationJournalError("SUBMISSION_IDENTITY_CHANGED")
            else:
                raise PreparationJournalError("INVALID_SUBMISSION_PHASE")
            key = intent.submission_id
            history = row.submissions.get(key)
            if (
                history
                and history.get("record_sha256")
                != PreparationSubmission(
                    submission.target_sha256,
                    replace(intent, phase=PreparationPhase.UNSENT),
                    submission.ledger,
                ).record_sha256
            ):
                raise PreparationJournalError("SUBMISSION_HISTORY_CHANGED")
            normalized = replace(intent, phase=PreparationPhase.UNSENT)
            row.submissions[key] = {
                "state": intent.phase.value,
                "path": intent.path,
                "method": intent.method,
                "record_sha256": PreparationSubmission(
                    submission.target_sha256, normalized, submission.ledger
                ).record_sha256,
            }
            row.ledger, row.ledger_sha256, row.state = payload, submission.ledger.sha256, intent.phase.value
            self._save(row, accepted)
            return PreparationCommitReceipt(
                str(row.guid),
                row.version,
                reservation.operation_id,
                row.preparation_sha256,
                row.ledger_sha256,
                submission.record_sha256,
                intent.phase,
            )

    def commit_observation(
        self,
        reservation: PreparationReservation,
        observation: ObjectObservation,
        *,
        checkpoint: Checkpoint,
        iam: IAMBinding | None = None,
    ) -> PreparationCommitReceipt:
        if (
            type(observation) is not ObjectObservation
            or observation.operation_id != reservation.operation_id
            or observation.target_sha256 != observation.ledger.target_sha256
            or observation.object not in observation.ledger.objects
        ):
            raise PreparationJournalError("INVALID_OBJECT_OBSERVATION")
        payload = self._validated(observation.ledger)
        with self._locked(self._operation(reservation), checkpoint, iam=iam) as (row, accepted, iam_row):
            row = self._bound(row, reservation)
            old = self._ledger(row)
            if row.state == row.State.OBSERVED:
                raise PreparationJournalError("TERMINAL_OPERATION")
            obj = observation.object
            pending = next((item for item in old.pending if item.path == obj.path), None)
            prior = next((item for item in old.objects if item.path == obj.path), None)
            if (
                tuple(item for item in old.objects if item.path != obj.path)
                != tuple(item for item in observation.ledger.objects if item.path != obj.path)
                or tuple(item for item in old.pending if item.path != obj.path) != observation.ledger.pending
            ):
                raise PreparationJournalError("OBSERVATION_LEDGER_CHANGED")
            if pending and pending.phase != PreparationPhase.SENT:
                raise PreparationJournalError("UNSENT_OWNERSHIP_REFUSED")
            if pending and pending.method == "PATCH":
                self._annotation_current(row, accepted, iam, iam_row)
            if prior is not None:
                if obj.uid != prior.uid or obj.creation_operation_id != prior.creation_operation_id:
                    raise PreparationJournalError("ORIGINAL_UID_CHANGED")
            elif pending is not None:
                if pending.method != "POST" or obj.creation_operation_id != reservation.operation_id:
                    raise PreparationJournalError("CREATE_OWNERSHIP_UNPROVED")
            else:
                pinned = {
                    f"/api/v1/namespaces/{subject.namespace}": subject.namespace_uid
                    for subject in self.target.context.subjects
                }
                pinned.update(
                    {
                        f"/api/v1/namespaces/{subject.namespace}/serviceaccounts/{subject.name}": subject.service_account_uid
                        for subject in self.target.context.subjects
                    }
                )
                if not pinned.get(obj.path) or pinned[obj.path] != obj.uid or obj.creation_operation_id:
                    raise PreparationJournalError("CREATE_OWNERSHIP_UNPROVED")
            if pending:
                history = row.submissions.get(pending.submission_id)
                if not history or history.get("state") != "SENT":
                    raise PreparationJournalError("SUBMISSION_HISTORY_CHANGED")
                row.submissions[pending.submission_id] = {**history, "state": "OBSERVED"}
            row.ledger, row.ledger_sha256 = payload, observation.ledger.sha256
            row.state = (
                row.State.SENT
                if any(item.phase == PreparationPhase.SENT for item in observation.ledger.pending)
                else row.State.UNSENT
                if observation.ledger.pending
                else row.State.RESERVED
            )
            self._save(row, accepted)
            return PreparationCommitReceipt(
                str(row.guid),
                row.version,
                reservation.operation_id,
                row.preparation_sha256,
                row.ledger_sha256,
                observation.record_sha256,
                PreparationPhase.OBSERVED,
            )

    def record_result(
        self,
        reservation: PreparationReservation,
        receipt: GKEPreparationReceipt,
        *,
        stage: PreparationStage,
        checkpoint: Checkpoint,
        completed: bool = False,
        iam: IAMBinding | None = None,
    ) -> None:
        if (
            type(receipt) is not GKEPreparationReceipt
            or type(stage) is not PreparationStage
            or type(completed) is not bool
        ):
            raise PreparationJournalError("INVALID_PREPARATION_RESULT")
        self._validated(receipt.ledger)
        with self._locked(self._operation(reservation), checkpoint, iam=iam) as (row, accepted, iam_row):
            row = self._bound(row, reservation)
            if row.state == row.State.OBSERVED:
                if completed and stage == PreparationStage.ANNOTATE and self._ledger(row) == receipt.ledger:
                    self._annotation_current(row, accepted, iam, iam_row)
                    return
                raise PreparationJournalError("TERMINAL_OPERATION")
            if self._ledger(row) != receipt.ledger:
                raise PreparationJournalError("RESULT_LEDGER_CHANGED")
            if completed:
                if (
                    not receipt.configuration_observed
                    or not self._complete_objects(receipt.ledger)
                    or any(step.get("state") != "OBSERVED" for step in row.submissions.values())
                ):
                    raise PreparationJournalError("PREPARATION_NOT_COMPLETED")
                if stage == PreparationStage.ANNOTATE:
                    self._annotation_current(row, accepted, iam, iam_row)
                    row.state, row.observed_revision, row.observed_at = (
                        row.State.OBSERVED,
                        row.desired_revision,
                        timezone.now(),
                    )
                else:
                    row.state, row.prepared_revision, row.prepared_at = (
                        row.State.PREPARED,
                        row.desired_revision,
                        timezone.now(),
                    )
            else:
                row.state = (
                    row.State.UNKNOWN
                    if any(item.phase == PreparationPhase.SENT for item in receipt.ledger.pending)
                    else row.State.UNSENT
                    if receipt.ledger.pending
                    else row.State.RESERVED
                )
            self._save(row, accepted)


@contextmanager
def preparation_journal_mutex(target: PreparationTarget) -> Iterator[PreparationStore]:
    _outside_atomic()
    if type(target) is not PreparationTarget or connection.vendor != "postgresql":
        raise PreparationJournalError("POSTGRES_PREPARATION_MUTEX_REQUIRED")
    identity = (
        "astrolift-gcp-gke-preparation-v1",
        target.context.identity.app_id,
        target.context.identity.cluster_id,
    )
    lock_id = int.from_bytes(hashlib.sha256(json.dumps(identity).encode()).digest()[:8], "big", signed=True)
    try:
        mutex = connection.Database.connect(**connection.get_connection_params())
    except connection.Database.Error:
        raise PreparationJournalError("MUTEX_UNAVAILABLE") from None
    store = PreparationStore(target, mutex, lock_id)
    try:
        mutex.autocommit = True
        with mutex.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            if not cursor.fetchone()[0]:
                raise PreparationJournalError("PREPARATION_BUSY")
        yield store
    finally:
        store._active = False
        try:
            if not mutex.closed:
                with mutex.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
        finally:
            mutex.close()
