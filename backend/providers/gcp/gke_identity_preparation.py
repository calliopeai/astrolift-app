"""Durably admitted GKE namespace/KSA preparation; never IAM or workload rollout."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from gcp.gke_identity_observation import (
    GKEIdentityObserver,
    GKEObservationContext,
    GKEObservationError,
    KSASubject,
    KubernetesReadAdapter,
    _checkpoint,
    _http,
)
from gcp.identity_acknowledgement import AcknowledgementReceipt, PreparationAcknowledgement, acknowledgement_sha256
from gcp.identity_owned import MAX_BYTES, _guid, _hash

if TYPE_CHECKING:
    from collections.abc import Callable

_VERSION = re.compile(r"[!-~]{1,256}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LINK = "iam.gke.io/gcp-service-account"
_OPERATION = "astrolift.io/preparation-operation"
_REQUEST = "astrolift.io/preparation-request"


class PreparationPhase(StrEnum):
    UNSENT = "UNSENT"
    SENT = "SENT"
    OBSERVED = "OBSERVED"


@dataclass(frozen=True)
class PreparedObject:
    kind: str
    path: str
    uid: str
    resource_version: str
    creation_operation_id: str = ""


@dataclass(frozen=True)
class PreparationIntent:
    submission_id: str
    operation_id: str
    kind: str
    path: str
    method: str
    request_sha256: str
    wire_request_sha256: str
    original_uid: str = ""
    original_resource_version: str = ""
    phase: PreparationPhase = PreparationPhase.UNSENT


@dataclass(frozen=True)
class GKEPreparationLedger:
    target_sha256: str
    objects: tuple[PreparedObject, ...] = ()
    pending: tuple[PreparationIntent, ...] = ()

    @property
    def sha256(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True)
class PreparationSubmission:
    target_sha256: str
    intent: PreparationIntent
    ledger: GKEPreparationLedger

    @property
    def record_sha256(self) -> str:
        return _hash((self.target_sha256, asdict(self.intent)))


@dataclass(frozen=True)
class ObjectObservation:
    target_sha256: str
    operation_id: str
    object: PreparedObject
    ledger: GKEPreparationLedger

    @property
    def record_sha256(self) -> str:
        return _hash((self.target_sha256, self.operation_id, asdict(self.object)))


@dataclass(frozen=True)
class PreparationCommitReceipt:
    journal_id: str
    journal_version: int
    operation_id: str
    target_sha256: str
    ledger_sha256: str
    record_sha256: str
    phase: PreparationPhase


@dataclass(frozen=True)
class VerifiedIAMConfigurationReceipt:
    journal_id: str
    journal_version: int
    operation_id: str
    identity_sha256: str
    desired_union_sha256: str
    service_account_uids: tuple[str, ...]
    completed: bool


@dataclass(frozen=True)
class PreparationStep:
    submission_id: str
    path: str
    phase: str
    transport_invoked: bool = False


@dataclass(frozen=True)
class GKEPreparationReceipt:
    ledger: GKEPreparationLedger
    steps: tuple[PreparationStep, ...] = ()
    configuration_observed: bool = False
    workload_ready: bool = False
    impersonation_verified: bool = False


class GKEPreparationError(GKEObservationError):
    def __init__(self, reason: str, receipt: GKEPreparationReceipt) -> None:
        super().__init__(reason)
        self.receipt = receipt


def preparation_target_sha256(context: GKEObservationContext) -> str:
    return _hash(
        (
            context.identity.fingerprint,
            context.cluster_resource,
            context.native_cluster_id,
            tuple(asdict(row) for row in context.subjects),
        )
    )


def _path(subject: KSASubject, namespace: bool) -> str:
    root = "/api/v1/namespaces/" + subject.namespace
    return root if namespace else root + "/serviceaccounts/" + subject.name


def _body_sha(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class KubernetesPreparationAdapter(KubernetesReadAdapter):
    """Private native endpoint/CA/ADC adapter; bounded exact Kubernetes operations."""

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Any = None,
        acknowledge: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        if not re.fullmatch(r"/api/v1/namespaces(?:/[a-z0-9-]{1,63}(?:/serviceaccounts(?:/[a-z0-9-]{1,63})?)?)?", path):
            raise GKEObservationError("INVALID_SUBJECT_PATH")
        if method not in ("GET", "POST", "PATCH"):
            raise GKEObservationError("METHOD_REFUSED")
        _checkpoint(self.checkpoint)
        try:
            if not self.credentials.valid:
                self.credentials.refresh(self.credential_request)
            _checkpoint(self.checkpoint)
            token = self.credentials.token
            if not isinstance(token, str) or not token or len(token) > 16384 or any(c in token for c in "\r\n"):
                raise GKEObservationError("CREDENTIAL_UNAVAILABLE")
            data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() if body is not None else None
            if data is not None and len(data) > MAX_BYTES:
                raise GKEObservationError("OVERSIZED_REQUEST")
            response = _http(
                self.base_url + path,
                method=method,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                    "Content-Type": "application/json-patch+json" if method == "PATCH" else "application/json",
                },
                data=data,
                context=self.tls,
            )
        except Exception:
            _checkpoint(self.checkpoint)
            raise GKEObservationError("KUBERNETES_TRANSPORT_UNCONFIRMED") from None
        if response.status == 404 and method == "GET":
            _checkpoint(self.checkpoint)
            return 404, None
        if response.status not in (200, 201):
            _checkpoint(self.checkpoint)
            raise GKEObservationError("KUBERNETES_OPERATION_REFUSED")
        try:
            value = json.loads(response.data)
        except Exception:
            _checkpoint(self.checkpoint)
            raise GKEObservationError("KUBERNETES_RESPONSE_INVALID") from None
        if not isinstance(value, dict):
            _checkpoint(self.checkpoint)
            raise GKEObservationError("KUBERNETES_RESPONSE_INVALID")
        if acknowledge is not None:
            if method not in ("POST", "PATCH"):
                raise GKEObservationError("ACKNOWLEDGEMENT_METHOD_INVALID")
            acknowledge(value)
        _checkpoint(self.checkpoint)
        return response.status, value


class GKEIdentityPreparation:
    """Trusted durable hooks are required; typed objects alone prove no persistence."""

    def __init__(
        self,
        context: GKEObservationContext,
        *,
        clients: tuple[Any, Any] | None = None,
        kubernetes_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.context = context
        self.observer = GKEIdentityObserver(context, clients=clients)
        self.factory = kubernetes_factory or KubernetesPreparationAdapter

    def close(self) -> None:
        self.observer.close()

    def __enter__(self) -> GKEIdentityPreparation:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _create_body(self, subject: KSASubject, namespace: bool, operation_id: str) -> tuple[dict[str, Any], str]:
        metadata: dict[str, Any] = {
            "name": subject.namespace if namespace else subject.name,
            "labels": self.context.owner_labels,
            "annotations": {_OPERATION: operation_id},
        }
        if not namespace:
            metadata["namespace"] = subject.namespace
        body: Any = {
            "apiVersion": "v1",
            "kind": "Namespace" if namespace else "ServiceAccount",
            "metadata": metadata,
        }
        digest = _body_sha(body)
        metadata["annotations"][_REQUEST] = digest
        return body, digest

    def _validate(self, ledger: GKEPreparationLedger) -> None:
        paths = {_path(s, ns): (s, ns) for s in self.context.subjects for ns in (True, False)}
        namespace_uids: dict[str, str] = {}
        for subject in self.context.subjects:
            if subject.service_account_uid and not subject.namespace_uid:
                raise GKEObservationError("NAMESPACE_UID_UNRECORDED")
            if subject.namespace in namespace_uids and namespace_uids[subject.namespace] != subject.namespace_uid:
                raise GKEObservationError("INCOHERENT_NAMESPACE_UID")
            namespace_uids[subject.namespace] = subject.namespace_uid
        if len({row.uid for row in ledger.objects}) != len(ledger.objects):
            raise GKEObservationError("DUPLICATE_SUBJECT_UID")
        if (
            ledger.target_sha256 != preparation_target_sha256(self.context)
            or len(ledger.objects) > 128
            or len(ledger.pending) > 128
        ):
            raise GKEObservationError("INVALID_PREPARATION_LEDGER")
        for rows in (ledger.objects, ledger.pending):
            if len({row.path for row in rows}) != len(rows):
                raise GKEObservationError("DUPLICATE_SUBJECT")
            for row in rows:
                if row.path not in paths or row.kind != ("Namespace" if paths[row.path][1] else "ServiceAccount"):
                    raise GKEObservationError("FOREIGN_SUBJECT")
                if isinstance(row, PreparedObject):
                    _guid(row.uid)
                    if not _VERSION.fullmatch(row.resource_version):
                        raise GKEObservationError("INVALID_RESOURCE_VERSION")
                    if row.creation_operation_id:
                        _guid(row.creation_operation_id)
                    original = (
                        paths[row.path][0].namespace_uid
                        if paths[row.path][1]
                        else paths[row.path][0].service_account_uid
                    )
                    if original and original != row.uid:
                        raise GKEObservationError("SUBJECT_UID_CHANGED")
                    if not original and not row.creation_operation_id:
                        raise GKEObservationError("CREATE_OWNERSHIP_UNPROVED")
                else:
                    _guid(row.submission_id)
                    _guid(row.operation_id)
                    if (
                        row.phase not in (PreparationPhase.UNSENT, PreparationPhase.SENT)
                        or type(row.phase) is not PreparationPhase
                        or not _DIGEST.fullmatch(row.request_sha256)
                        or not _DIGEST.fullmatch(row.wire_request_sha256)
                    ):
                        raise GKEObservationError("INVALID_SUBMISSION")
                    if (
                        row.method not in ("POST", "PATCH")
                        or (row.method == "POST" and bool(row.original_uid or row.original_resource_version))
                        or (row.method == "PATCH" and (not row.original_uid or row.kind != "ServiceAccount"))
                    ):
                        raise GKEObservationError("INVALID_SUBMISSION")
                    if row.method == "POST":
                        subject, namespace = paths[row.path]
                        original = subject.namespace_uid if namespace else subject.service_account_uid
                        if original or any(o.path == row.path for o in ledger.objects):
                            raise GKEObservationError("INVALID_SUBMISSION")
                        body, digest = self._create_body(subject, namespace, row.operation_id)
                        if row.request_sha256 != digest or row.wire_request_sha256 != _body_sha(body):
                            raise GKEObservationError("PENDING_REQUEST_CHANGED")
                    if row.method == "PATCH":
                        recorded = next((o for o in ledger.objects if o.path == row.path), None)
                        if (
                            recorded is None
                            or recorded.uid != row.original_uid
                            or recorded.resource_version != row.original_resource_version
                        ):
                            raise GKEObservationError("INVALID_SUBMISSION")
                    if row.original_uid:
                        _guid(row.original_uid)
                        if not _VERSION.fullmatch(row.original_resource_version):
                            raise GKEObservationError("INVALID_RESOURCE_VERSION")

    def _metadata(
        self, value: dict[str, Any], subject: KSASubject, *, namespace: bool, uid: str = ""
    ) -> PreparedObject:
        metadata = value.get("metadata")
        path = _path(subject, namespace)
        if not isinstance(metadata, dict):
            raise GKEObservationError("SUBJECT_RESPONSE_INVALID")
        actual_uid, rv = metadata.get("uid"), metadata.get("resourceVersion")
        if (
            value.get("apiVersion") != "v1"
            or value.get("kind") != ("Namespace" if namespace else "ServiceAccount")
            or metadata.get("name") != (subject.namespace if namespace else subject.name)
            or (not namespace and metadata.get("namespace") != subject.namespace)
            or not isinstance(actual_uid, str)
            or metadata.get("deletionTimestamp")
            or not isinstance(rv, str)
            or not _VERSION.fullmatch(rv)
            or not isinstance(metadata.get("labels"), dict)
            or any(metadata["labels"].get(k) != v for k, v in self.context.owner_labels.items())
            or (uid and actual_uid != uid)
        ):
            raise GKEObservationError("SUBJECT_OWNERSHIP_UNVERIFIED")
        _guid(actual_uid)
        annotations = metadata.get("annotations", {})
        if not isinstance(annotations, dict) or (
            not namespace and annotations.get(_LINK, "") not in ("", self.context.identity.email)
        ):
            raise GKEObservationError("SERVICE_ACCOUNT_LINK_FOREIGN")
        return PreparedObject(value["kind"], path, actual_uid, rv)

    def _run(
        self,
        *,
        operation_id: str,
        ledger: GKEPreparationLedger,
        commit_submission: Callable[[PreparationSubmission], PreparationCommitReceipt],
        commit_observation: Callable[[ObjectObservation], PreparationCommitReceipt],
        checkpoint: Callable[[], None],
        iam_receipt: VerifiedIAMConfigurationReceipt | None,
        expected_desired_union_sha256: str | None,
        acknowledgement_hook: Callable[[PreparationAcknowledgement], AcknowledgementReceipt] | None,
        acknowledgements: tuple[PreparationAcknowledgement, ...],
        strict_acknowledgements: bool,
    ) -> GKEPreparationReceipt:
        steps: dict[str, PreparationStep] = {}
        journal_id, version = "", 0
        acknowledged = {}
        if (
            type(strict_acknowledgements) is not bool
            or type(acknowledgements) is not tuple
            or any(type(value) is not PreparationAcknowledgement for value in acknowledgements)
        ):
            raise GKEPreparationError("ACKNOWLEDGEMENT_INVALID", GKEPreparationReceipt(ledger))
        if not strict_acknowledgements and (acknowledgement_hook is not None or acknowledgements):
            raise GKEPreparationError("ACKNOWLEDGEMENT_MODE_REQUIRED", GKEPreparationReceipt(ledger))
        if strict_acknowledgements and not callable(acknowledgement_hook):
            raise GKEPreparationError("ACKNOWLEDGEMENT_HOOK_REQUIRED", GKEPreparationReceipt(ledger))
        for value in acknowledgements:
            if value.submission_id in acknowledged:
                raise GKEPreparationError("ACKNOWLEDGEMENT_INVALID", GKEPreparationReceipt(ledger))
            acknowledged[value.submission_id] = value

        def acknowledge(intent: PreparationIntent, subject: KSASubject, namespace: bool, value: dict[str, Any]) -> None:
            actual = self._metadata(value, subject, namespace=namespace, uid=intent.original_uid)
            annotations = value["metadata"].get("annotations", {})
            if intent.method == "POST" and (
                annotations.get(_OPERATION) != operation_id or annotations.get(_REQUEST) != intent.request_sha256
            ):
                raise GKEObservationError("CREATE_OWNERSHIP_UNPROVED")
            if intent.method == "PATCH" and annotations.get(_LINK) != self.context.identity.email:
                raise GKEObservationError("SERVICE_ACCOUNT_LINK_UNCONFIRMED")
            ack = PreparationAcknowledgement(
                intent.submission_id, intent.request_sha256, actual.uid, actual.resource_version
            )
            try:
                if acknowledgement_hook is None:
                    raise ValueError
                receipt = acknowledgement_hook(ack)
            except Exception:
                raise GKEObservationError("ACKNOWLEDGEMENT_COMMIT_UNCONFIRMED") from None
            if type(receipt) is not AcknowledgementReceipt or receipt.acknowledgement_sha256 != acknowledgement_sha256(
                ack
            ):
                raise GKEObservationError("ACKNOWLEDGEMENT_RECEIPT_REQUIRED")
            acknowledged[intent.submission_id] = ack

        def write(
            adapter: Any, intent: PreparationIntent, subject: KSASubject, namespace: bool, path: str, body: Any
        ) -> None:
            if strict_acknowledgements:
                adapter.request(
                    path,
                    method=intent.method,
                    body=body,
                    acknowledge=lambda value: acknowledge(intent, subject, namespace, value),
                )
            else:
                adapter.request(path, method=intent.method, body=body)

        def note(intent: PreparationIntent, phase: str, invoked: bool = False) -> None:
            old = steps.get(intent.path)
            steps[intent.path] = PreparationStep(
                intent.submission_id, intent.path, phase, invoked or bool(old and old.transport_invoked)
            )

        def commit(record: PreparationSubmission | ObjectObservation) -> None:
            nonlocal journal_id, version, ledger
            _checkpoint(checkpoint)
            phase = record.intent.phase if isinstance(record, PreparationSubmission) else PreparationPhase.OBSERVED
            if isinstance(record, PreparationSubmission):
                note(record.intent, "UNKNOWN" if phase == PreparationPhase.SENT else "UNSENT")
            try:
                receipt = (
                    commit_submission(record)
                    if isinstance(record, PreparationSubmission)
                    else commit_observation(record)
                )
            except Exception:
                raise GKEObservationError("JOURNAL_COMMIT_UNCONFIRMED") from None
            if (
                type(receipt) is not PreparationCommitReceipt
                or type(receipt.journal_version) is not int
                or receipt.journal_version <= version
                or receipt.operation_id != operation_id
                or receipt.target_sha256 != ledger.target_sha256
                or receipt.ledger_sha256 != record.ledger.sha256
                or receipt.record_sha256 != record.record_sha256
                or type(receipt.phase) is not PreparationPhase
                or receipt.phase != phase
            ):
                raise GKEObservationError("DURABLE_RECEIPT_REQUIRED")
            _guid(receipt.journal_id)
            if journal_id and journal_id != receipt.journal_id:
                raise GKEObservationError("JOURNAL_CHANGED")
            journal_id, version, ledger = receipt.journal_id, receipt.journal_version, record.ledger
            if isinstance(record, PreparationSubmission):
                note(record.intent, phase.value)
            _checkpoint(checkpoint)

        def admit() -> Any:
            cluster, _ = self.observer._cluster(checkpoint)
            return self.factory(
                cluster.endpoint, cluster.master_auth.cluster_ca_certificate, self.observer._credentials, checkpoint
            )

        def observe(
            value: dict[str, Any], subject: KSASubject, namespace: bool, uid: str = "", creation_id: str = ""
        ) -> PreparedObject:
            row = replace(
                self._metadata(value, subject, namespace=namespace, uid=uid), creation_operation_id=creation_id
            )
            next_ledger = replace(
                ledger,
                objects=(*tuple(o for o in ledger.objects if o.path != row.path), row),
                pending=tuple(p for p in ledger.pending if p.path != row.path),
            )
            commit(ObjectObservation(ledger.target_sha256, operation_id, row, next_ledger))
            return row

        try:
            _checkpoint(checkpoint)
            _guid(operation_id)
            self._validate(ledger)
            for intent in ledger.pending:
                if intent.operation_id != operation_id:
                    raise GKEObservationError("PENDING_OPERATION_CHANGED")
                note(intent, "UNKNOWN" if intent.phase == PreparationPhase.SENT else "UNSENT")
                if (
                    strict_acknowledgements
                    and intent.phase == PreparationPhase.SENT
                    and intent.method == "POST"
                    and intent.submission_id not in acknowledged
                ):
                    raise GKEObservationError("CREATE_UID_ACKNOWLEDGEMENT_REQUIRED")
                ack = acknowledged.get(intent.submission_id)
                if ack is not None and (
                    ack.request_sha256 != intent.request_sha256
                    or (intent.original_uid and ack.uid != intent.original_uid)
                ):
                    raise GKEObservationError("ACKNOWLEDGEMENT_SUBMISSION_CHANGED")
            if iam_receipt is not None:
                self._iam(iam_receipt, operation_id, ledger, expected_desired_union_sha256)
            adapter = admit()
            # Resolve every older send before introducing any new effect.
            for old_intent in tuple(ledger.pending):
                if old_intent.phase != PreparationPhase.SENT:
                    continue
                subject = next(
                    s for s in self.context.subjects if _path(s, old_intent.kind == "Namespace") == old_intent.path
                )
                namespace = old_intent.kind == "Namespace"
                _, value = adapter.request(old_intent.path)
                _checkpoint(checkpoint)
                if value is None:
                    raise GKEObservationError("SENT_SUBMISSION_UNRESOLVED")
                row = next((o for o in ledger.objects if o.path == old_intent.path), None)
                if old_intent.method == "POST":
                    annotations = value.get("metadata", {}).get("annotations", {})
                    if (
                        annotations.get(_OPERATION) != operation_id
                        or annotations.get(_REQUEST) != old_intent.request_sha256
                    ):
                        raise GKEObservationError("CREATE_OWNERSHIP_UNPROVED")
                    ack = acknowledged.get(old_intent.submission_id)
                    original_uid = row.uid if row else (ack.uid if strict_acknowledgements and ack else "")
                    observe(value, subject, namespace, original_uid, operation_id)
                else:
                    if iam_receipt is None:
                        raise GKEObservationError("IAM_CONFIGURATION_RECEIPT_REQUIRED")
                    actual = self._metadata(value, subject, namespace=False, uid=old_intent.original_uid)
                    if value["metadata"].get("annotations", {}).get(_LINK) != self.context.identity.email:
                        raise GKEObservationError("SENT_SUBMISSION_UNRESOLVED")
                    observe(value, subject, False, actual.uid, row.creation_operation_id if row else "")
                note(old_intent, "OBSERVED")
                adapter = admit()
            for subject in self.context.subjects:
                for namespace in (True, False):
                    path = _path(subject, namespace)
                    pending = next((p for p in ledger.pending if p.path == path), None)
                    row = next((o for o in ledger.objects if o.path == path), None)
                    original_uid = subject.namespace_uid if namespace else subject.service_account_uid
                    _, value = adapter.request(path)
                    _checkpoint(checkpoint)
                    if pending and pending.method == "PATCH":
                        if iam_receipt is None:
                            raise GKEObservationError("IAM_CONFIGURATION_RECEIPT_REQUIRED")
                        if value is None:
                            raise GKEObservationError("SUBJECT_UNOBSERVED")
                        actual = self._metadata(value, subject, namespace=namespace, uid=pending.original_uid)
                        linked = value["metadata"].get("annotations", {}).get(_LINK, "")
                        if pending.phase == PreparationPhase.SENT:
                            if linked != self.context.identity.email:
                                raise GKEObservationError("SENT_SUBMISSION_UNRESOLVED")
                            row = observe(
                                value, subject, namespace, actual.uid, row.creation_operation_id if row else ""
                            )
                            note(pending, "OBSERVED")
                            continue
                        if linked == self.context.identity.email:
                            raise GKEObservationError("UNSENT_SUBJECT_CONFLICT")
                        if actual.resource_version != pending.original_resource_version:
                            raise GKEObservationError("UNSENT_SUBJECT_CONFLICT")
                    elif value is not None:
                        if row or original_uid:
                            if pending:
                                raise GKEObservationError("PENDING_SUBJECT_CONFLICT")
                            row = observe(
                                value,
                                subject,
                                namespace,
                                row.uid if row else original_uid,
                                row.creation_operation_id if row else "",
                            )
                        elif pending and pending.phase == PreparationPhase.SENT:
                            annotations = value.get("metadata", {}).get("annotations", {})
                            if (
                                annotations.get(_OPERATION) != operation_id
                                or annotations.get(_REQUEST) != pending.request_sha256
                            ):
                                raise GKEObservationError("CREATE_OWNERSHIP_UNPROVED")
                            ack = acknowledged.get(pending.submission_id)
                            if strict_acknowledgements and ack is None:
                                raise GKEObservationError("CREATE_UID_ACKNOWLEDGEMENT_REQUIRED")
                            row = observe(
                                value,
                                subject,
                                namespace,
                                uid=ack.uid if strict_acknowledgements and ack else "",
                                creation_id=operation_id,
                            )
                            note(pending, "OBSERVED")
                        else:
                            raise GKEObservationError("EXISTING_SUBJECT_UID_UNRECORDED")
                    else:
                        if row or original_uid:
                            raise GKEObservationError("SUBJECT_UNOBSERVED")
                        if pending and pending.phase == PreparationPhase.SENT:
                            raise GKEObservationError("SENT_SUBMISSION_UNRESOLVED")
                        if iam_receipt is not None:
                            raise GKEObservationError("SUBJECT_UID_UNRECORDED")
                        body, digest = self._create_body(subject, namespace, operation_id)
                        intent = pending or PreparationIntent(
                            str(uuid4()), operation_id, body["kind"], path, "POST", digest, _body_sha(body)
                        )
                        if intent.request_sha256 != digest or intent.wire_request_sha256 != _body_sha(body):
                            raise GKEObservationError("PENDING_REQUEST_CHANGED")
                        unsigned = replace(
                            ledger, pending=(*tuple(p for p in ledger.pending if p.path != path), intent)
                        )
                        commit(PreparationSubmission(ledger.target_sha256, intent, unsigned))
                        adapter = admit()
                        _, fresh = adapter.request(path)
                        if fresh is not None:
                            raise GKEObservationError("UNSENT_SUBJECT_CONFLICT")
                        sent = replace(intent, phase=PreparationPhase.SENT)
                        commit(
                            PreparationSubmission(
                                ledger.target_sha256,
                                sent,
                                replace(ledger, pending=(*tuple(p for p in ledger.pending if p.path != path), sent)),
                            )
                        )
                        _checkpoint(checkpoint)
                        note(sent, "UNKNOWN", True)
                        write(adapter, sent, subject, namespace, path.rsplit("/", 1)[0], body)
                        adapter = admit()
                        _, fresh = adapter.request(path)
                        if (
                            fresh is None
                            or fresh.get("metadata", {}).get("annotations", {}).get(_REQUEST) != digest
                            or fresh.get("metadata", {}).get("annotations", {}).get(_OPERATION) != operation_id
                        ):
                            raise GKEObservationError("CREATE_READBACK_UNCONFIRMED")
                        ack = acknowledged.get(sent.submission_id)
                        row = observe(
                            fresh,
                            subject,
                            namespace,
                            uid=ack.uid if strict_acknowledgements and ack else "",
                            creation_id=operation_id,
                        )
                        note(sent, "OBSERVED")
                    if not namespace and iam_receipt is not None:
                        adapter = admit()
                        _, fresh = adapter.request(path)
                        if fresh is None or row is None:
                            raise GKEObservationError("SUBJECT_UNOBSERVED")
                        actual = self._metadata(fresh, subject, namespace=False, uid=row.uid)
                        annotations = fresh["metadata"].get("annotations", {})
                        if annotations.get(_LINK, "") == self.context.identity.email:
                            continue
                        patch: list[dict[str, Any]] = [
                            {"op": "test", "path": "/metadata/uid", "value": actual.uid},
                            {"op": "test", "path": "/metadata/resourceVersion", "value": actual.resource_version},
                        ]
                        if "annotations" in fresh["metadata"]:
                            patch += [
                                {"op": "test", "path": "/metadata/annotations", "value": annotations},
                                {
                                    "op": "add",
                                    "path": "/metadata/annotations/iam.gke.io~1gcp-service-account",
                                    "value": self.context.identity.email,
                                },
                            ]
                        else:
                            patch += [
                                {
                                    "op": "add",
                                    "path": "/metadata/annotations",
                                    "value": {_LINK: self.context.identity.email},
                                }
                            ]
                        digest = _body_sha(patch)
                        intent = pending or PreparationIntent(
                            str(uuid4()),
                            operation_id,
                            "ServiceAccount",
                            path,
                            "PATCH",
                            digest,
                            digest,
                            actual.uid,
                            actual.resource_version,
                        )
                        if intent.request_sha256 != digest or intent.wire_request_sha256 != digest:
                            raise GKEObservationError("PENDING_REQUEST_CHANGED")
                        commit(
                            PreparationSubmission(
                                ledger.target_sha256,
                                intent,
                                replace(ledger, pending=(*tuple(p for p in ledger.pending if p.path != path), intent)),
                            )
                        )
                        adapter = admit()
                        sent = replace(intent, phase=PreparationPhase.SENT)
                        commit(
                            PreparationSubmission(
                                ledger.target_sha256,
                                sent,
                                replace(ledger, pending=(*tuple(p for p in ledger.pending if p.path != path), sent)),
                            )
                        )
                        _checkpoint(checkpoint)
                        note(sent, "UNKNOWN", True)
                        write(adapter, sent, subject, False, path, patch)
                        adapter = admit()
                        _, fresh = adapter.request(path)
                        if (
                            fresh is None
                            or fresh.get("metadata", {}).get("annotations", {}).get(_LINK)
                            != self.context.identity.email
                        ):
                            raise GKEObservationError("ANNOTATION_READBACK_UNCONFIRMED")
                        row = observe(fresh, subject, False, actual.uid, row.creation_operation_id)
                        note(sent, "OBSERVED")
            adapter = admit()
            for row in ledger.objects:
                _, fresh = adapter.request(row.path)
                subject = next(s for s in self.context.subjects if _path(s, row.kind == "Namespace") == row.path)
                if fresh is None:
                    raise GKEObservationError("SUBJECT_UNOBSERVED")
                actual = self._metadata(fresh, subject, namespace=row.kind == "Namespace", uid=row.uid)
                if actual.resource_version != row.resource_version:
                    raise GKEObservationError("SUBJECT_OBSERVATION_CHANGED")
                if (
                    iam_receipt is not None
                    and row.kind == "ServiceAccount"
                    and fresh["metadata"].get("annotations", {}).get(_LINK) != self.context.identity.email
                ):
                    raise GKEObservationError("ANNOTATION_READBACK_UNCONFIRMED")
            admit()
            _checkpoint(checkpoint)
            return GKEPreparationReceipt(ledger, tuple(steps.values()), configuration_observed=True)
        except GKEObservationError as error:
            raise GKEPreparationError(str(error), GKEPreparationReceipt(ledger, tuple(steps.values()))) from None
        except Exception:
            raise GKEPreparationError(
                "PREPARATION_UNCONFIRMED", GKEPreparationReceipt(ledger, tuple(steps.values()))
            ) from None

    def _iam(
        self,
        receipt: VerifiedIAMConfigurationReceipt,
        operation_id: str,
        ledger: GKEPreparationLedger,
        expected_desired_union_sha256: str | None,
    ) -> None:
        if (
            type(receipt) is not VerifiedIAMConfigurationReceipt
            or type(receipt.journal_version) is not int
            or receipt.journal_version <= 0
            or receipt.operation_id != operation_id
            or receipt.completed is not True
            or receipt.identity_sha256 != self.context.identity.fingerprint
            or not isinstance(expected_desired_union_sha256, str)
            or not _DIGEST.fullmatch(expected_desired_union_sha256)
            or receipt.desired_union_sha256 != expected_desired_union_sha256
        ):
            raise GKEObservationError("IAM_CONFIGURATION_RECEIPT_REQUIRED")
        _guid(receipt.journal_id)
        expected = tuple(sorted(row.uid for row in ledger.objects if row.kind == "ServiceAccount"))
        if (
            len(expected) != len(self.context.subjects)
            or len(set(expected)) != len(expected)
            or tuple(sorted(receipt.service_account_uids)) != expected
        ):
            raise GKEObservationError("IAM_SUBJECT_UID_CHANGED")

    def prepare(
        self,
        *,
        operation_id: str,
        ledger: GKEPreparationLedger,
        commit_submission: Callable[[PreparationSubmission], PreparationCommitReceipt],
        commit_observation: Callable[[ObjectObservation], PreparationCommitReceipt],
        checkpoint: Callable[[], None],
        acknowledgement_hook: Callable[[PreparationAcknowledgement], AcknowledgementReceipt] | None = None,
        acknowledgements: tuple[PreparationAcknowledgement, ...] = (),
        strict_acknowledgements: bool = False,
    ) -> GKEPreparationReceipt:
        return self._run(
            operation_id=operation_id,
            ledger=ledger,
            commit_submission=commit_submission,
            commit_observation=commit_observation,
            checkpoint=checkpoint,
            iam_receipt=None,
            expected_desired_union_sha256=None,
            acknowledgement_hook=acknowledgement_hook,
            acknowledgements=acknowledgements,
            strict_acknowledgements=strict_acknowledgements,
        )

    def annotate(
        self,
        *,
        operation_id: str,
        ledger: GKEPreparationLedger,
        commit_submission: Callable[[PreparationSubmission], PreparationCommitReceipt],
        commit_observation: Callable[[ObjectObservation], PreparationCommitReceipt],
        checkpoint: Callable[[], None],
        iam_receipt: VerifiedIAMConfigurationReceipt,
        expected_desired_union_sha256: str,
        acknowledgement_hook: Callable[[PreparationAcknowledgement], AcknowledgementReceipt] | None = None,
        acknowledgements: tuple[PreparationAcknowledgement, ...] = (),
        strict_acknowledgements: bool = False,
    ) -> GKEPreparationReceipt:
        if type(iam_receipt) is not VerifiedIAMConfigurationReceipt:
            raise GKEPreparationError("IAM_CONFIGURATION_RECEIPT_REQUIRED", GKEPreparationReceipt(ledger))
        return self._run(
            operation_id=operation_id,
            ledger=ledger,
            commit_submission=commit_submission,
            commit_observation=commit_observation,
            checkpoint=checkpoint,
            iam_receipt=iam_receipt,
            expected_desired_union_sha256=expected_desired_union_sha256,
            acknowledgement_hook=acknowledgement_hook,
            acknowledgements=acknowledgements,
            strict_acknowledgements=strict_acknowledgements,
        )
