"""Short committed GCP reservations; authority and native observations are supplied ports.

The mutex connection never touches a parent or journal row. No method permits an
outer transaction, a lease takeover, or a SENT intent to be treated as unsent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.utils import timezone
from gcp.identity_owned import (
    DurableSubmissionReceipt,
    OwnedGrant,
    OwnedGrantLedger,
    PolicyIntent,
    PolicyOwnership,
    PolicyReconcileReceipt,
    PolicyStepReceipt,
    PolicyStepState,
    PolicySubmission,
    PolicySubmissionPhase,
    owned_ledger_from_payload,
    owned_ledger_payload,
)

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import GCPWorkloadIdentityJournal

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_NUMBER = re.compile(r"[1-9][0-9]{0,31}\Z")
_LOCATION = re.compile(r"[a-z]+(?:-[a-z0-9]+)+[0-9](?:-[a-z])?\Z")
_MAX_LEDGER = 2 * 1024 * 1024


class JournalError(ValueError):
    """Fixed reasons only; never return native bodies or caller metadata."""


def _uuid(value: str) -> str:
    try:
        parsed = UUID(value)
        if str(parsed) == value and parsed.int:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise JournalError("INVALID_IDENTITY")


def _digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise JournalError("INVALID_DIGEST")
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _outside_atomic() -> None:
    if connection.in_atomic_block or not connection.get_autocommit():
        raise JournalError("ENCLOSING_TRANSACTION_REFUSED")


@dataclass(frozen=True)
class KSAIdentity:
    environment_id: str
    namespace: str
    name: str
    namespace_uid: str
    service_account_uid: str

    def __post_init__(self) -> None:
        for value in (self.environment_id, self.namespace_uid, self.service_account_uid):
            _uuid(value)
        if not all(isinstance(v, str) and _NAME.fullmatch(v) for v in (self.namespace, self.name)):
            raise JournalError("INVALID_KSA_IDENTITY")


@dataclass(frozen=True)
class JournalTarget:
    organization_id: str
    app_id: str
    cluster_id: str
    provider_id: str
    project_id: str
    project_number: str
    region: str
    gsa_id: str
    gsa_unique_id: str
    gke_location: str
    gke_name: str
    gke_cluster_id: str
    credential_declaration_sha256: str
    context_sha256: str
    ksa_identities: tuple[KSAIdentity, ...]

    def __post_init__(self) -> None:
        for value in (self.organization_id, self.app_id, self.cluster_id, self.provider_id):
            _uuid(value)
        for value in (self.credential_declaration_sha256, self.context_sha256):
            _digest(value)
        if any(
            not isinstance(v, str)
            for v in (
                self.project_id,
                self.project_number,
                self.region,
                self.gsa_id,
                self.gsa_unique_id,
                self.gke_location,
                self.gke_name,
                self.gke_cluster_id,
            )
        ):
            raise JournalError("INVALID_TARGET")
        if (
            not _PROJECT.fullmatch(self.project_id)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", self.project_number)
            or not _PROJECT.fullmatch(self.gsa_id)
            or not _NUMBER.fullmatch(self.gsa_unique_id)
            or not _LOCATION.fullmatch(self.region)
            or not _LOCATION.fullmatch(self.gke_location)
            or not _NAME.fullmatch(self.gke_name)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.gke_cluster_id)
            or not isinstance(self.ksa_identities, tuple)
            or not 1 <= len(self.ksa_identities) <= 64
            or any(not isinstance(row, KSAIdentity) for row in self.ksa_identities)
            or len({row.environment_id for row in self.ksa_identities}) != len(self.ksa_identities)
            or len({row.service_account_uid for row in self.ksa_identities}) != len(self.ksa_identities)
        ):
            raise JournalError("INVALID_TARGET")

    @property
    def payload(self) -> dict[str, Any]:
        return {"schema_version": 1, **asdict(self)}

    @property
    def sha256(self) -> str:
        return _hash(self.payload)


@dataclass(frozen=True)
class OperationIdentity:
    operation_id: str
    workflow_id: str
    execution_id: str
    desired_revision: int
    desired_union_sha256: str
    authority_reference_sha256: str

    def __post_init__(self) -> None:
        _uuid(self.operation_id)
        for value in (self.desired_union_sha256, self.authority_reference_sha256):
            _digest(value)
        if (
            type(self.desired_revision) is not int
            or self.desired_revision < 1
            or self.desired_revision > 2**63 - 1
        ):
            raise JournalError("INVALID_REVISION")
        if any(
            not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", v)
            for v in (self.workflow_id, self.execution_id)
        ):
            raise JournalError("INVALID_OPERATION")


@dataclass(frozen=True)
class Reservation:
    journal_id: str
    operation_id: str
    generation: int
    nonce: str
    target_sha256: str
    desired_union_sha256: str
    authority_reference_sha256: str

    def __post_init__(self) -> None:
        for value in (self.journal_id, self.operation_id, self.nonce):
            _uuid(value)
        for value in (self.target_sha256, self.desired_union_sha256, self.authority_reference_sha256):
            _digest(value)
        if type(self.generation) is not int or self.generation < 1:
            raise JournalError("INVALID_RESERVATION")


@dataclass(frozen=True)
class CompletedConfiguration:
    """Actual committed metadata observed under the current reservation fence."""

    journal_id: str
    journal_version: int
    generation: int
    target: JournalTarget
    operation: OperationIdentity
    ledger_sha256: str


@dataclass(frozen=True)
class JournalCheckpoint:
    organization: Organization
    team: Team
    project: Project | None
    app: RegisteredApp
    cluster: TenantCluster
    provider: ProviderPlugin
    journal: GCPWorkloadIdentityJournal | None
    target: JournalTarget
    operation: OperationIdentity


Checkpoint = Callable[[JournalCheckpoint], None]


class JournalStore:
    def __init__(self, target: JournalTarget, mutex_connection: Any, lock_id: int):
        self.target = target
        self._mutex_connection = mutex_connection
        self._lock_id = lock_id
        self._active = True

    def _ready(self) -> None:
        _outside_atomic()
        if not self._active or self._mutex_connection.closed:
            raise JournalError("MUTEX_NOT_HELD")
        with self._mutex_connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype = %s AND pid = pg_backend_pid() AND classid = %s AND objid = %s AND objsubid = 1 AND granted)",
                ["advisory", (self._lock_id >> 32) & 0xFFFFFFFF, self._lock_id & 0xFFFFFFFF],
            )
            if not cursor.fetchone()[0]:
                raise JournalError("MUTEX_NOT_HELD")

    @contextmanager
    def _locked(
        self, operation: OperationIdentity, checkpoint: Checkpoint
    ) -> Iterator[GCPWorkloadIdentityJournal | None]:
        self._ready()
        if not callable(checkpoint):
            raise JournalError("CURRENT_AUTHORITY_REQUIRED")
        target = self.target
        with transaction.atomic():
            try:
                org = Organization._unscoped.select_for_update().get(
                    guid=target.organization_id, deleted_at__isnull=True
                )
                cluster = TenantCluster._unscoped.select_for_update().get(
                    guid=target.cluster_id, deleted_at__isnull=True
                )
                provider = ProviderPlugin._unscoped.select_for_update().get(
                    guid=target.provider_id, deleted_at__isnull=True
                )
                observed = RegisteredApp._unscoped.get(guid=target.app_id)
                team = Team._unscoped.select_for_update().get(
                    pk=observed.team_id, organization_id=org.pk, deleted_at__isnull=True
                )
                project = None
                if observed.project_id:
                    project = Project._unscoped.select_for_update().get(
                        pk=observed.project_id,
                        organization_id=org.pk,
                        team_id=team.pk,
                        deleted_at__isnull=True,
                    )
                app = RegisteredApp._unscoped.select_for_update().get(
                    guid=target.app_id,
                    organization_id=org.pk,
                    team_id=team.pk,
                    project_id=observed.project_id,
                    deleted_at__isnull=True,
                )
            except (
                Organization.DoesNotExist,
                Team.DoesNotExist,
                Project.DoesNotExist,
                RegisteredApp.DoesNotExist,
                TenantCluster.DoesNotExist,
                ProviderPlugin.DoesNotExist,
            ):
                raise JournalError("CURRENT_OWNER_UNAVAILABLE") from None
            if (
                cluster.organization_id not in (None, org.pk)
                or cluster.provider_plugin_id != provider.pk
                or provider.slug != "gcp"
            ):
                raise JournalError("CURRENT_TARGET_CHANGED")
            rows = GCPWorkloadIdentityJournal._unscoped.select_for_update().filter(
                registered_app=app, tenant_cluster=cluster
            )
            if rows.filter(deleted_at__isnull=False).exists():
                raise JournalError("RETIRED_JOURNAL_REQUIRES_REVIEW")
            journal = rows.filter(deleted_at__isnull=True).first()
            if journal and (
                journal.organization_id != org.pk
                or journal.provider_plugin_id != provider.pk
                or journal.target_sha256 != target.sha256
                or journal.target_snapshot != json.loads(json.dumps(target.payload))
                or journal.context_sha256 != target.context_sha256
            ):
                raise JournalError("ORIGINAL_TARGET_CHANGED")
            context = JournalCheckpoint(
                org, team, project, app, cluster, provider, journal, target, operation
            )
            if checkpoint(context) is not None:
                raise JournalError("CURRENT_AUTHORITY_UNCONFIRMED")
            self._parents = (org, app, cluster, provider)
            yield journal

    def _bound(
        self, journal: GCPWorkloadIdentityJournal | None, reservation: Reservation
    ) -> GCPWorkloadIdentityJournal:
        if journal is None or (
            str(journal.guid),
            str(journal.operation_id),
            journal.generation,
            str(journal.reservation_nonce),
            journal.target_sha256,
            journal.desired_union_sha256,
            journal.authority_reference_sha256,
        ) != (
            reservation.journal_id,
            reservation.operation_id,
            reservation.generation,
            reservation.nonce,
            reservation.target_sha256,
            reservation.desired_union_sha256,
            reservation.authority_reference_sha256,
        ):
            raise JournalError("STALE_RESERVATION")
        return journal

    def _operation(self, reservation: Reservation) -> OperationIdentity:
        self._ready()
        row = GCPWorkloadIdentityJournal._unscoped.filter(guid=reservation.journal_id).first()
        if row is None:
            raise JournalError("STALE_RESERVATION")
        return OperationIdentity(
            reservation.operation_id,
            row.workflow_id,
            row.execution_id,
            row.desired_revision,
            reservation.desired_union_sha256,
            reservation.authority_reference_sha256,
        )

    @staticmethod
    def _reservation(journal: GCPWorkloadIdentityJournal) -> Reservation:
        return Reservation(
            str(journal.guid),
            str(journal.operation_id),
            journal.generation,
            str(journal.reservation_nonce),
            journal.target_sha256,
            journal.desired_union_sha256,
            journal.authority_reference_sha256,
        )

    def reserve(
        self, operation: OperationIdentity, *, checkpoint: Checkpoint, expected_version: int | None = None
    ) -> Reservation:
        if not isinstance(operation, OperationIdentity):
            raise JournalError("INVALID_OPERATION")
        with self._locked(operation, checkpoint) as row:
            if row is not None and str(row.operation_id) == operation.operation_id:
                if (
                    row.workflow_id,
                    row.execution_id,
                    row.desired_revision,
                    row.desired_union_sha256,
                    row.authority_reference_sha256,
                ) != (
                    operation.workflow_id,
                    operation.execution_id,
                    operation.desired_revision,
                    operation.desired_union_sha256,
                    operation.authority_reference_sha256,
                ):
                    raise JournalError("OPERATION_IDENTITY_CHANGED")
                return self._reservation(row)
            if row is not None:
                ledger = self._ledger(row)
                if row.state != row.State.OBSERVED or ledger.pending or ledger.removals:
                    raise JournalError("UNRESOLVED_OPERATION")
                if expected_version != row.version or operation.desired_revision <= row.desired_revision:
                    raise JournalError("STALE_REVISION")
                row.generation += 1
                row.submissions = {}
            else:
                if expected_version is not None:
                    raise JournalError("STALE_REVISION")
                org, app, cluster, provider = self._parents
                row = GCPWorkloadIdentityJournal(
                    organization=org,
                    registered_app=app,
                    tenant_cluster=cluster,
                    provider_plugin=provider,
                    target_snapshot=self.target.payload,
                    target_sha256=self.target.sha256,
                    context_sha256=self.target.context_sha256,
                )
                ledger = OwnedGrantLedger(self.target.context_sha256)
                row.ledger = owned_ledger_payload(ledger)
                row.ledger_sha256 = _hash(row.ledger)
            row.operation_id = operation.operation_id
            row.reservation_nonce = uuid4()
            row.workflow_id = operation.workflow_id
            row.execution_id = operation.execution_id
            row.desired_revision = operation.desired_revision
            row.desired_union_sha256 = operation.desired_union_sha256
            row.authority_reference_sha256 = operation.authority_reference_sha256
            row.state = row.State.RESERVED
            row.save()
            return self._reservation(row)

    def validate_current(self, reservation: Reservation, *, checkpoint: Checkpoint) -> None:
        with self._locked(self._operation(reservation), checkpoint) as row:
            self._bound(row, reservation)

    def read(self, reservation: Reservation, *, checkpoint: Checkpoint) -> OwnedGrantLedger:
        with self._locked(self._operation(reservation), checkpoint) as row:
            return self._ledger(self._bound(row, reservation))

    def completed_configuration(
        self, reservation: Reservation, *, checkpoint: Checkpoint
    ) -> CompletedConfiguration:
        operation = self._operation(reservation)
        with self._locked(operation, checkpoint) as locked:
            row = self._bound(locked, reservation)
            ledger = self._ledger(row)
            if (
                row.state != row.State.OBSERVED
                or row.observed_revision != row.desired_revision
                or row.observed_at is None
                or ledger.pending
                or ledger.removals
                or not isinstance(row.submissions, dict)
                or len(row.submissions) > 65
                or any(
                    not isinstance(step, dict) or step.get("state") != "OBSERVED"
                    for step in row.submissions.values()
                )
            ):
                raise JournalError("CONFIGURATION_NOT_COMPLETED")
            result = CompletedConfiguration(
                str(row.guid), row.version, row.generation, self.target, operation, row.ledger_sha256
            )
        return result

    def _ledger(self, row: GCPWorkloadIdentityJournal) -> OwnedGrantLedger:
        if _hash(row.ledger) != row.ledger_sha256:
            raise JournalError("JOURNAL_INTEGRITY_FAILURE")
        try:
            ledger = owned_ledger_from_payload(row.ledger)
            self._validated(ledger)
            return ledger
        except ValueError:
            raise JournalError("JOURNAL_INTEGRITY_FAILURE") from None

    def _validated(self, ledger: OwnedGrantLedger) -> dict[str, Any]:
        if not isinstance(ledger, OwnedGrantLedger):
            raise JournalError("INVALID_LEDGER")
        for rows, expected in (
            (ledger.policies, PolicyOwnership),
            (ledger.pending, PolicyIntent),
            (ledger.removals, PolicyOwnership),
        ):
            if not isinstance(rows, tuple) or len(rows) > 65:
                raise JournalError("INVALID_LEDGER")
            for item in rows:
                if (
                    not isinstance(item, expected)
                    or not isinstance(item.resource, str)
                    or len(item.resource) > 256
                ):
                    raise JournalError("INVALID_LEDGER")
                grants = item.owned_after if isinstance(item, PolicyIntent) else item.grants
                if not isinstance(grants, tuple) or len(grants) > 1024:
                    raise JournalError("INVALID_LEDGER")
                if any(
                    not isinstance(grant, OwnedGrant)
                    or not isinstance(grant.role, str)
                    or not isinstance(grant.member, str)
                    or len(grant.role) > 128
                    or len(grant.member) > 384
                    for grant in grants
                ):
                    raise JournalError("INVALID_LEDGER")
        try:
            payload = owned_ledger_payload(ledger)
            size = len(json.dumps(payload).encode())
        except (TypeError, ValueError, AttributeError):
            raise JournalError("INVALID_LEDGER") from None
        if ledger.context_sha256 != self.target.context_sha256 or size > _MAX_LEDGER:
            raise JournalError("INVALID_LEDGER")
        try:
            owned_ledger_from_payload(payload)
        except ValueError:
            raise JournalError("INVALID_LEDGER") from None
        gsa = f"projects/{self.target.project_id}/serviceAccounts/{self.target.gsa_unique_id}"
        endpoint = re.compile(
            rf"projects/{self.target.project_number}/locations/{self.target.region}/endpoints/(?:[a-z](?:[a-z0-9-]{{0,61}}[a-z0-9])?|[1-9][0-9]{{0,19}})\Z"
        )
        principals = {
            f"principal://iam.googleapis.com/projects/{self.target.project_number}/locations/global/workloadIdentityPools/{self.target.project_id}.svc.id.goog/kubernetes.serviceaccount.uid/{item.service_account_uid}"
            for item in self.target.ksa_identities
        }
        for rows in (ledger.policies, ledger.pending, ledger.removals):
            if len({item.resource for item in rows}) != len(rows):
                raise JournalError("INVALID_LEDGER")
            for item in rows:
                is_gsa = item.resource == gsa
                if not is_gsa and not endpoint.fullmatch(item.resource):
                    raise JournalError("FOREIGN_LEDGER_RESOURCE")
                grants = item.owned_after if hasattr(item, "owned_after") else item.grants
                if len(set(grants)) != len(grants):
                    raise JournalError("INVALID_LEDGER")
                for grant in grants:
                    valid = (
                        (grant.role == "roles/iam.workloadIdentityUser" and grant.member in principals)
                        if is_gsa
                        else (
                            bool(
                                re.fullmatch(
                                    rf"projects/{self.target.project_id}/roles/[A-Za-z0-9_.]{{1,64}}",
                                    grant.role,
                                )
                            )
                            and grant.member
                            == f"serviceAccount:{self.target.gsa_id}@{self.target.project_id}.iam.gserviceaccount.com"
                        )
                    )
                    if not valid:
                        raise JournalError("FOREIGN_LEDGER_GRANT")
                if hasattr(item, "before_sha256"):
                    if item.before_sha256 == item.after_sha256:
                        raise JournalError("UNCHANGED_SUBMISSION_REFUSED")
                    for digest in (
                        item.before_sha256,
                        item.after_sha256,
                        item.etag_sha256,
                        item.desired_union_sha256,
                    ):
                        _digest(digest)
                    _uuid(item.submission_id)
                    if item.submission_phase not in (
                        PolicySubmissionPhase.UNSENT,
                        PolicySubmissionPhase.SENT,
                    ):
                        raise JournalError("UNKNOWN_SUBMISSION_REQUIRES_REVIEW")
        return payload

    def commit_submission(
        self, reservation: Reservation, submission: PolicySubmission, *, checkpoint: Checkpoint
    ) -> DurableSubmissionReceipt:
        if not isinstance(submission, PolicySubmission):
            raise JournalError("INVALID_SUBMISSION")
        payload = self._validated(submission.ledger)
        intent = submission.intent
        _uuid(intent.submission_id)
        if (
            submission.context_sha256 != self.target.context_sha256
            or intent not in submission.ledger.pending
            or intent.desired_union_sha256 != reservation.desired_union_sha256
        ):
            raise JournalError("SUBMISSION_IDENTITY_CHANGED")
        with self._locked(self._operation(reservation), checkpoint) as locked:
            row = self._bound(locked, reservation)
            old = self._ledger(row)
            old_pending = {item.resource: item for item in old.pending}
            new_pending = {item.resource: item for item in submission.ledger.pending}
            prior = old_pending.get(intent.resource)
            old_owned = {item.resource: set(item.grants) for item in old.policies}
            old_removals = {item.resource: set(item.grants) for item in old.removals}
            if old.policies != submission.ledger.policies or {
                k: v for k, v in old_pending.items() if k != intent.resource
            } != {k: v for k, v in new_pending.items() if k != intent.resource}:
                raise JournalError("SUBMISSION_LEDGER_CHANGED")
            new_removals = {item.resource: set(item.grants) for item in submission.ledger.removals}
            if any(
                not grants <= old_owned.get(resource, set()) | old_removals.get(resource, set())
                for resource, grants in new_removals.items()
            ) or any(
                not grants <= new_removals.get(resource, set()) for resource, grants in old_removals.items()
            ):
                raise JournalError("REMOVAL_OWNERSHIP_REQUIRED")
            if any(
                item.submission_phase == PolicySubmissionPhase.SENT for item in old.pending
            ) or row.state in (row.State.SENT, row.State.UNKNOWN):
                raise JournalError("SENT_SUBMISSION_UNRESOLVED")
            if intent.submission_phase == PolicySubmissionPhase.SENT:
                if (
                    prior is None
                    or prior.submission_phase != PolicySubmissionPhase.UNSENT
                    or PolicySubmission(self.target.context_sha256, prior, old).submission_sha256
                    != submission.submission_sha256
                ):
                    raise JournalError("UNSENT_RESERVATION_REQUIRED")
            elif intent.submission_phase == PolicySubmissionPhase.UNSENT:
                if prior is not None and (
                    prior.submission_phase != PolicySubmissionPhase.UNSENT
                    or PolicySubmission(self.target.context_sha256, prior, old).submission_sha256
                    != submission.submission_sha256
                ):
                    raise JournalError("SUBMISSION_IDENTITY_CHANGED")
            else:
                raise JournalError("INVALID_SUBMISSION_PHASE")
            if (
                intent.submission_id in row.submissions
                and row.submissions[intent.submission_id]["submission_sha256"] != submission.submission_sha256
            ):
                raise JournalError("SUBMISSION_IDENTITY_CHANGED")
            if len(row.submissions) >= 65 and intent.submission_id not in row.submissions:
                raise JournalError("SUBMISSION_BOUND_EXCEEDED")
            row.submissions[intent.submission_id] = {
                "resource": intent.resource,
                "submission_sha256": submission.submission_sha256,
                "state": str(intent.submission_phase),
            }
            row.state = str(intent.submission_phase)
            row.ledger = payload
            row.ledger_sha256 = _hash(payload)
            row.save()
            return DurableSubmissionReceipt(
                str(row.guid),
                row.version,
                intent.submission_id,
                submission.submission_sha256,
                intent.submission_phase,
                row.ledger_sha256,
            )

    def persist_ledger(
        self, reservation: Reservation, ledger: OwnedGrantLedger, *, checkpoint: Checkpoint
    ) -> None:
        payload = self._validated(ledger)
        with self._locked(self._operation(reservation), checkpoint) as locked:
            row = self._bound(locked, reservation)
            old = self._ledger(row)
            if any(item not in old.pending for item in ledger.pending):
                raise JournalError("SUBMISSION_HOOK_REQUIRED")
            prior_owned = {item.resource: set(item.grants) for item in old.policies}
            prior_removals = {item.resource: set(item.grants) for item in old.removals}
            pending_by_resource = {item.resource: item for item in old.pending}
            for policy in ledger.policies:
                pending = pending_by_resource.get(policy.resource)
                allowed = (
                    set(pending.owned_after)
                    if pending and pending not in ledger.pending
                    else prior_owned.get(policy.resource, set())
                )
                if not set(policy.grants) <= allowed:
                    raise JournalError("OWNERSHIP_INTENT_REQUIRED")
            if any(
                not set(item.grants)
                <= prior_owned.get(item.resource, set()) | prior_removals.get(item.resource, set())
                for item in ledger.removals
            ):
                raise JournalError("REMOVAL_OWNERSHIP_REQUIRED")
            policies = {item.resource: item.grants for item in ledger.policies}
            for intent in old.pending:
                if intent not in ledger.pending:
                    if intent.submission_phase != PolicySubmissionPhase.SENT:
                        raise JournalError("UNSENT_OWNERSHIP_REFUSED")
                    if tuple(policies.get(intent.resource, ())) != intent.owned_after:
                        raise JournalError("OBSERVED_OWNERSHIP_REQUIRED")
                    step = row.submissions.get(intent.submission_id)
                    if (
                        step is None
                        or step["submission_sha256"]
                        != PolicySubmission(self.target.context_sha256, intent, old).submission_sha256
                    ):
                        raise JournalError("UNKNOWN_SUBMISSION_REQUIRES_REVIEW")
                    step["state"] = "OBSERVED"
            row.ledger = payload
            row.ledger_sha256 = _hash(payload)
            row.state = (
                row.State.UNKNOWN
                if any(item.submission_phase == PolicySubmissionPhase.SENT for item in ledger.pending)
                else (row.State.UNSENT if ledger.pending else row.State.RESERVED)
            )
            row.save()

    def record_result(
        self,
        reservation: Reservation,
        receipt: PolicyReconcileReceipt,
        *,
        checkpoint: Checkpoint,
        completed: bool = False,
    ) -> None:
        if not isinstance(receipt, PolicyReconcileReceipt) or type(completed) is not bool:
            raise JournalError("INVALID_RESULT")
        if any(
            not isinstance(step, PolicyStepReceipt)
            or not isinstance(step.state, PolicyStepState)
            or type(step.transport_invoked) is not bool
            for step in receipt.steps
        ):
            raise JournalError("INVALID_RESULT")
        self._validated(receipt.ledger)
        with self._locked(self._operation(reservation), checkpoint) as locked:
            row = self._bound(locked, reservation)
            if _hash(owned_ledger_payload(receipt.ledger)) != row.ledger_sha256 or len(receipt.steps) > 65:
                raise JournalError("RESULT_IDENTITY_CHANGED")
            seen = set()
            for step in receipt.steps:
                stored = row.submissions.get(step.submission_id)
                if (
                    step.submission_id in seen
                    or stored is None
                    or (stored["resource"], stored["submission_sha256"])
                    != (step.resource, step.submission_sha256)
                ):
                    raise JournalError("RESULT_IDENTITY_CHANGED")
                seen.add(step.submission_id)
                if stored["state"] in ("SENT", "UNKNOWN") and step.state == PolicyStepState.UNSENT:
                    raise JournalError("SENT_SUBMISSION_UNRESOLVED")
                if step.state == PolicyStepState.OBSERVED and stored["state"] != "OBSERVED":
                    raise JournalError("OBSERVED_OWNERSHIP_REQUIRED")
                stored["state"] = str(step.state)
            ledger = self._ledger(row)
            if (
                any(item["state"] in ("SENT", "UNKNOWN") for item in row.submissions.values())
                or ledger.pending
            ):
                row.state = row.State.UNKNOWN
            elif (
                not completed
                or ledger.removals
                or any(item["state"] != "OBSERVED" for item in row.submissions.values())
            ):
                row.state = row.State.RESERVED
            else:
                row.state = row.State.OBSERVED
                row.observed_revision = row.desired_revision
                row.observed_at = timezone.now()
            row.save()


@contextmanager
def journal_mutex(target: JournalTarget) -> Iterator[JournalStore]:
    _outside_atomic()
    if connection.vendor != "postgresql":
        raise JournalError("POSTGRESQL_REQUIRED")
    identity = (
        "astrolift-gcp-workload-identity-v1",
        target.app_id,
        target.cluster_id,
    )
    lock_id = int.from_bytes(hashlib.sha256(json.dumps(identity).encode()).digest()[:8], "big", signed=True)
    try:
        mutex = connection.Database.connect(**connection.get_connection_params())
    except connection.Database.Error:
        raise JournalError("MUTEX_UNAVAILABLE") from None
    store = JournalStore(target, mutex, lock_id)
    try:
        mutex.autocommit = True
        with mutex.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            if not cursor.fetchone()[0]:
                raise JournalError("JOURNAL_BUSY")
        yield store
    finally:
        store._active = False
        try:
            if not mutex.closed:
                with mutex.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
        finally:
            mutex.close()
