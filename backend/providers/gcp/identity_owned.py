"""Owned Endpoint IAM union; the caller must durably journal every write intent.

This port does not create identities, configure GKE or prove pod/invoke access.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from _sdk.cloud_credentials import CloudCredential, CredentialMode

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

TIMEOUT = 10.0
MAX_BYTES = 2 * 1024 * 1024
MAX_RESOURCES = 65
MAX_BINDINGS = 256
MAX_MEMBERS = 1024
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_NUMBER = re.compile(r"[1-9][0-9]{0,19}\Z")
_UNIQUE_ID = re.compile(r"[1-9][0-9]{0,31}\Z")
_REGION = re.compile(r"[a-z]+(?:-[a-z0-9]+)+[0-9]\Z")
_ACCOUNT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_ROLE_ID = re.compile(r"[A-Za-z0-9_.]{1,64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ENDPOINT_ID = r"(?:[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?|[1-9][0-9]{0,19})"


class NativeIdentityError(ValueError):
    """Sanitized fixed reasons; native exception text must not escape."""


def _guid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise NativeIdentityError("INVALID_GUID") from None
    if str(parsed) != value or parsed.int == 0:
        raise NativeIdentityError("INVALID_GUID")
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class NativeIdentityContext:
    organization_id: str
    app_id: str
    cluster_id: str
    project_id: str
    project_number: str
    region: str
    service_account_id: str
    service_account_unique_id: str
    credential: CloudCredential = field(repr=False)

    def __post_init__(self) -> None:
        for guid in (self.organization_id, self.app_id, self.cluster_id):
            _guid(guid)
        credential = self.credential
        if (
            not _PROJECT.fullmatch(self.project_id)
            or not _NUMBER.fullmatch(self.project_number)
            or not _REGION.fullmatch(self.region)
            or not _ACCOUNT.fullmatch(self.service_account_id)
            or not _UNIQUE_ID.fullmatch(self.service_account_unique_id)
            or credential.cloud != "gcp"
            or credential.mode != CredentialMode.AMBIENT
            or credential.declared_account not in (self.project_id, self.project_number)
            or credential.role_arn
            or credential.external_id
        ):
            raise NativeIdentityError("INVALID_CONTEXT")

    @property
    def email(self) -> str:
        return f"{self.service_account_id}@{self.project_id}.iam.gserviceaccount.com"

    @property
    def service_account_resource(self) -> str:
        return f"projects/{self.project_id}/serviceAccounts/{self.service_account_unique_id}"

    @property
    def owner_description(self) -> str:
        return "astrolift-owned-identity-v1:" + _hash(
            (self.organization_id, self.app_id, self.cluster_id, self.project_id, self.project_number)
        )

    @property
    def fingerprint(self) -> str:
        return _hash(
            (
                self.owner_description,
                self.region,
                self.email,
                self.service_account_unique_id,
                self.credential.declared_account,
            )
        )

    def principal(self, service_account_uid: str) -> str:
        return (
            f"principal://iam.googleapis.com/projects/{self.project_number}/locations/global/"
            f"workloadIdentityPools/{self.project_id}.svc.id.goog/kubernetes.serviceaccount.uid/{_guid(service_account_uid)}"
        )


@dataclass(frozen=True, order=True)
class OwnedGrant:
    role: str
    member: str


@dataclass(frozen=True)
class PolicyOwnership:
    resource: str
    grants: tuple[OwnedGrant, ...] = ()


class PolicySubmissionPhase(StrEnum):
    UNSENT = "UNSENT"
    SENT = "SENT"


class PolicyStepState(StrEnum):
    UNSENT = "UNSENT"
    SENT = "SENT"
    UNKNOWN = "UNKNOWN"
    OBSERVED = "OBSERVED"


@dataclass(frozen=True)
class PolicyIntent:
    resource: str
    before_sha256: str
    after_sha256: str
    owned_after: tuple[OwnedGrant, ...]
    submission_id: str = ""
    submission_phase: PolicySubmissionPhase | None = None
    etag_sha256: str = ""
    desired_union_sha256: str = ""


@dataclass(frozen=True)
class OwnedGrantLedger:
    context_sha256: str
    policies: tuple[PolicyOwnership, ...] = ()
    pending: tuple[PolicyIntent, ...] = ()
    removals: tuple[PolicyOwnership, ...] = ()


def owned_ledger_payload(ledger: OwnedGrantLedger) -> dict[str, Any]:
    return {"schema_version": 2, **asdict(ledger)}


def owned_ledger_from_payload(payload: dict[str, Any]) -> OwnedGrantLedger:
    try:
        if (
            set(payload) != {"schema_version", "context_sha256", "policies", "pending", "removals"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 2
            or len(json.dumps(payload).encode()) > MAX_BYTES
            or not isinstance(payload["context_sha256"], str)
            or not _DIGEST.fullmatch(payload["context_sha256"])
        ):
            raise ValueError

        def grants(rows: Any) -> tuple[OwnedGrant, ...]:
            if not isinstance(rows, (tuple, list)) or len(rows) > MAX_MEMBERS:
                raise ValueError
            result = []
            for row in rows:
                if set(row) != {"role", "member"} or any(not isinstance(v, str) for v in row.values()):
                    raise ValueError
                result.append(OwnedGrant(**row))
            return tuple(result)

        result: dict[str, Any] = {}
        for key in ("policies", "pending", "removals"):
            rows = payload[key]
            if not isinstance(rows, (tuple, list)) or len(rows) > MAX_RESOURCES:
                raise ValueError
            values: list[Any] = []
            for row in rows:
                if key == "pending":
                    expected = {
                        "resource",
                        "before_sha256",
                        "after_sha256",
                        "owned_after",
                        "submission_id",
                        "submission_phase",
                        "etag_sha256",
                        "desired_union_sha256",
                    }
                    if set(row) != expected or any(
                        not isinstance(v, str) for k, v in row.items() if k not in ("owned_after", "submission_phase")
                    ):
                        raise ValueError
                    phase = (
                        PolicySubmissionPhase(row["submission_phase"]) if row["submission_phase"] is not None else None
                    )
                    values.append(
                        PolicyIntent(**{**row, "owned_after": grants(row["owned_after"]), "submission_phase": phase})
                    )
                else:
                    if set(row) != {"resource", "grants"} or not isinstance(row["resource"], str):
                        raise ValueError
                    values.append(PolicyOwnership(row["resource"], grants(row["grants"])))
            result[key] = tuple(values)
        return OwnedGrantLedger(payload["context_sha256"], **result)
    except Exception:
        raise NativeIdentityError("INVALID_LEDGER_PAYLOAD") from None


@dataclass(frozen=True)
class PolicySubmission:
    context_sha256: str
    intent: PolicyIntent
    ledger: OwnedGrantLedger

    @property
    def ledger_sha256(self) -> str:
        return _hash(owned_ledger_payload(self.ledger))

    @property
    def submission_sha256(self) -> str:
        return _hash(
            (
                self.context_sha256,
                self.intent.submission_id,
                self.intent.resource,
                self.intent.before_sha256,
                self.intent.after_sha256,
                self.intent.etag_sha256,
                self.intent.desired_union_sha256,
                tuple((grant.role, grant.member) for grant in self.intent.owned_after),
            )
        )


@dataclass(frozen=True)
class DurableSubmissionReceipt:
    journal_id: str
    journal_version: int
    submission_id: str
    submission_sha256: str
    phase: PolicySubmissionPhase
    ledger_sha256: str


@dataclass(frozen=True)
class PolicyStepReceipt:
    submission_id: str
    resource: str
    submission_sha256: str
    state: PolicyStepState
    transport_invoked: bool = False


@dataclass(frozen=True)
class PolicyReconcileReceipt:
    ledger: OwnedGrantLedger
    steps: tuple[PolicyStepReceipt, ...] = ()


class NativeIdentityReconciliationError(NativeIdentityError):
    def __init__(self, reason: str, receipt: PolicyReconcileReceipt) -> None:
        super().__init__(reason)
        self.receipt = receipt


@dataclass(frozen=True)
class NativeIdentityResult:
    ledger: OwnedGrantLedger
    annotations: dict[str, str]
    external_equivalent_grants: int = 0
    workload_ready: bool = False
    receipt: PolicyReconcileReceipt | None = None


@dataclass
class _SubmissionProgress:
    ledger: OwnedGrantLedger
    steps: dict[str, PolicyStepReceipt] = field(default_factory=dict)
    journal_id: str = ""
    journal_version: int = 0

    def note(self, submission: PolicySubmission, state: PolicyStepState, *, invoked: bool | None = None) -> None:
        old = self.steps.get(submission.intent.resource)
        self.steps[submission.intent.resource] = PolicyStepReceipt(
            submission.intent.submission_id,
            submission.intent.resource,
            submission.submission_sha256,
            state,
            invoked if invoked is not None else bool(old and old.transport_invoked),
        )

    def receipt(self) -> PolicyReconcileReceipt:
        return PolicyReconcileReceipt(self.ledger, tuple(self.steps[key] for key in sorted(self.steps)))


class NativeGCPIdentity:
    """Injected clients are trusted native test ports, never caller API input.

    Checkpoint must re-admit current source, actor and exact placement. Persist
    must commit the supplied ledger before returning, including pending intents.
    """

    def __init__(self, context: NativeIdentityContext, *, clients: tuple[Any, Any, Any] | None = None) -> None:
        self.context = context
        self._clients = clients
        self._owned = clients is None
        self._closed = False

    def close(self) -> None:
        if not self._closed and self._owned and self._clients:
            for client in self._clients:
                client.transport.close()
        self._closed = True

    def __enter__(self) -> NativeGCPIdentity:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _native(self) -> tuple[Any, Any, Any]:
        if self._closed:
            raise NativeIdentityError("CLOSED")
        if self._clients is None:
            import google.auth
            import google.cloud.aiplatform_v1beta1 as aiplatform_v1beta1
            import google.cloud.iam_admin_v1 as iam_admin_v1
            import google.cloud.resourcemanager_v3 as resourcemanager_v3
            from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.base import (
                DEFAULT_CLIENT_INFO as EP_INFO,
            )
            from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.grpc import (
                EndpointServiceGrpcTransport,
            )
            from google.cloud.iam_admin_v1.services.iam.transports.base import DEFAULT_CLIENT_INFO as IAM_INFO
            from google.cloud.iam_admin_v1.services.iam.transports.grpc import IAMGrpcTransport
            from google.cloud.resourcemanager_v3.services.projects.transports.base import (
                DEFAULT_CLIENT_INFO as PROJECT_INFO,
            )
            from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            made: list[Any] = []
            try:
                for client_type, transport_type, host, client_info in (
                    (
                        resourcemanager_v3.ProjectsClient,
                        ProjectsGrpcTransport,
                        "cloudresourcemanager.googleapis.com",
                        PROJECT_INFO,
                    ),
                    (iam_admin_v1.IAMClient, IAMGrpcTransport, "iam.googleapis.com", IAM_INFO),
                    (
                        aiplatform_v1beta1.EndpointServiceClient,
                        EndpointServiceGrpcTransport,
                        f"{self.context.region}-aiplatform.googleapis.com",
                        EP_INFO,
                    ),
                ):
                    channel = transport_type.create_channel(
                        host, credentials=credentials, options=[("grpc.max_receive_message_length", MAX_BYTES)]
                    )
                    try:
                        transport = transport_type(host=host, channel=channel)
                        # Generated DEBUG interceptors expose IAM principals and
                        # full policies. Rebuild only these private transports.
                        transport._logged_channel = channel
                        transport._stubs.clear()
                        transport._wrapped_methods.clear()
                        transport._prep_wrapped_messages(client_info)
                        made.append(client_type(transport=transport))
                    except Exception:
                        channel.close()
                        raise
            except Exception:
                for client in made:
                    client.transport.close()
                raise
            self._clients = (made[0], made[1], made[2])
        return self._clients

    @staticmethod
    def _bounded(value: Any) -> None:
        serialize = getattr(type(value), "serialize", None)
        data = serialize(value) if serialize else value.SerializeToString()
        if len(data) > MAX_BYTES:
            raise NativeIdentityError("OVERSIZED_RESPONSE")

    @staticmethod
    def _checkpoint(checkpoint: Callable[[], object]) -> None:
        if checkpoint() is not None:
            raise NativeIdentityError("CURRENT_ADMISSION_UNCONFIRMED")

    def _call(self, fn: Any, request: Any, checkpoint: Callable[[], None]) -> Any:
        self._checkpoint(checkpoint)
        try:
            value = fn(request=request, retry=None, timeout=TIMEOUT)
        except Exception:
            self._checkpoint(checkpoint)
            raise NativeIdentityError("NATIVE_READ_OR_WRITE_UNCONFIRMED") from None
        self._checkpoint(checkpoint)
        self._bounded(value)
        return value

    def _admit(self, checkpoint: Callable[[], None]) -> None:
        self._checkpoint(checkpoint)
        projects, iam, _ = self._native()
        context = self.context
        project = self._call(projects.get_project, {"name": f"projects/{context.project_number}"}, checkpoint)
        if (
            project.name != f"projects/{context.project_number}"
            or project.project_id != context.project_id
            or project.state != 1
        ):
            raise NativeIdentityError("PROJECT_IDENTITY_CHANGED")
        account = self._call(iam.get_service_account, {"name": context.service_account_resource}, checkpoint)
        if (
            account.name != f"projects/{context.project_id}/serviceAccounts/{context.email}"
            or account.project_id != context.project_id
            or account.unique_id != context.service_account_unique_id
            or account.email != context.email
            or account.disabled
            or account.description != context.owner_description
        ):
            raise NativeIdentityError("SERVICE_ACCOUNT_OWNERSHIP_UNVERIFIED")

    def _resource(self, resource: str) -> str:
        context = self.context
        if resource == context.service_account_resource:
            return resource
        match = re.fullmatch(rf"projects/([^/]+)/locations/([^/]+)/endpoints/({_ENDPOINT_ID})", resource)
        if not match or match[1] not in (context.project_id, context.project_number) or match[2] != context.region:
            raise NativeIdentityError("INVALID_ENDPOINT_RESOURCE")
        return f"projects/{context.project_number}/locations/{context.region}/endpoints/{match[3]}"

    def _role(self, role: str) -> bool:
        prefix = f"projects/{self.context.project_id}/roles/"
        return role.startswith(prefix) and bool(_ROLE_ID.fullmatch(role[len(prefix) :]))

    def _verify_roles(self, roles: set[str], checkpoint: Callable[[], None]) -> None:
        _, iam, _ = self._native()
        for role in sorted(roles):
            observed = self._call(iam.get_role, {"name": role}, checkpoint)
            if (
                observed.name != role
                or observed.deleted
                or observed.stage != 2
                or set(observed.included_permissions) != {"aiplatform.endpoints.predict"}
            ):
                raise NativeIdentityError("PREDICTION_ROLE_UNVERIFIED")

    def _grant(self, resource: str, grant: OwnedGrant) -> None:
        if resource == self.context.service_account_resource:
            prefix = self.context.principal("00000000-0000-0000-0000-000000000001").rsplit("/", 1)[0] + "/"
            if grant.role != "roles/iam.workloadIdentityUser" or not grant.member.startswith(prefix):
                raise NativeIdentityError("INVALID_LEDGER_GRANT")
            _guid(grant.member[len(prefix) :])
        elif not self._role(grant.role) or grant.member != f"serviceAccount:{self.context.email}":
            raise NativeIdentityError("INVALID_LEDGER_GRANT")

    def _ledger(self, ledger: OwnedGrantLedger) -> None:
        if (
            ledger.context_sha256 != self.context.fingerprint
            or len(ledger.policies) > MAX_RESOURCES
            or len(ledger.pending) > MAX_RESOURCES
            or len(ledger.removals) > MAX_RESOURCES
        ):
            raise NativeIdentityError("INVALID_LEDGER_CONTEXT")
        for rows in (ledger.policies, ledger.pending, ledger.removals):
            resources = [row.resource for row in rows]
            if len(resources) != len(set(resources)):
                raise NativeIdentityError("DUPLICATE_LEDGER_RESOURCE")
            for row in rows:
                if self._resource(row.resource) != row.resource:
                    raise NativeIdentityError("NONCANONICAL_LEDGER_RESOURCE")
                grants = row.grants if isinstance(row, PolicyOwnership) else row.owned_after
                if len(grants) > MAX_MEMBERS or len(grants) != len(set(grants)):
                    raise NativeIdentityError("INVALID_LEDGER_GRANTS")
                for grant in grants:
                    self._grant(row.resource, grant)
                if isinstance(row, PolicyIntent) and not (
                    _DIGEST.fullmatch(row.before_sha256) and _DIGEST.fullmatch(row.after_sha256)
                ):
                    raise NativeIdentityError("INVALID_LEDGER_INTENT")
                if isinstance(row, PolicyIntent):
                    if row.submission_id:
                        _guid(row.submission_id)
                        if (
                            type(row.submission_phase) is not PolicySubmissionPhase
                            or not _DIGEST.fullmatch(row.etag_sha256)
                            or not _DIGEST.fullmatch(row.desired_union_sha256)
                        ):
                            raise NativeIdentityError("INVALID_SUBMISSION_INTENT")
                    elif row.submission_phase is not None or row.etag_sha256 or row.desired_union_sha256:
                        raise NativeIdentityError("INVALID_SUBMISSION_INTENT")

    @staticmethod
    def _policy_hash(policy: Any) -> str:
        clone = type(policy)()
        clone.CopyFrom(policy)
        clone.ClearField("etag")
        return hashlib.sha256(clone.SerializeToString(deterministic=True)).hexdigest()

    @staticmethod
    def _policy(policy: Any) -> None:
        if (
            policy.version not in (1, 3)
            or not policy.etag
            or len(policy.etag) > 256
            or len(policy.bindings) > MAX_BINDINGS
        ):
            raise NativeIdentityError("INCOMPLETE_POLICY")
        if sum(len(binding.members) for binding in policy.bindings) > MAX_MEMBERS:
            raise NativeIdentityError("OVERSIZED_POLICY")
        if any(binding.HasField("condition") for binding in policy.bindings) and policy.version != 3:
            raise NativeIdentityError("CONDITIONAL_POLICY_VERSION_REQUIRED")

    def reconcile(
        self,
        permissions: Iterable[dict[str, str]],
        *,
        service_account_uids: tuple[str, ...],
        ledger: OwnedGrantLedger,
        checkpoint: Callable[[], None],
        persist: Callable[[OwnedGrantLedger], None],
        submission_hook: Callable[[PolicySubmission], DurableSubmissionReceipt] | None = None,
    ) -> NativeIdentityResult:
        if submission_hook is None and any(row.submission_id for row in ledger.pending):
            raise NativeIdentityError("TYPED_SUBMISSION_HOOK_REQUIRED")
        progress = _SubmissionProgress(ledger)
        if submission_hook is not None:
            for intent in ledger.pending:
                submission = PolicySubmission(self.context.fingerprint, intent, ledger)
                progress.note(
                    submission,
                    PolicyStepState.UNKNOWN
                    if intent.submission_phase != PolicySubmissionPhase.UNSENT
                    else PolicyStepState.UNSENT,
                )

        def tracked_persist(value: OwnedGrantLedger) -> None:
            try:
                persist(value)
            except Exception:
                if submission_hook is None:
                    raise
                raise NativeIdentityError("JOURNAL_PERSISTENCE_UNCONFIRMED") from None
            progress.ledger = value

        try:
            result = self._reconcile(
                permissions,
                service_account_uids=service_account_uids,
                ledger=ledger,
                checkpoint=checkpoint,
                persist=tracked_persist,
                submission_hook=submission_hook,
                progress=progress,
            )
            return replace(result, receipt=progress.receipt()) if submission_hook is not None else result
        except NativeIdentityError as error:
            if submission_hook is None:
                raise
            raise NativeIdentityReconciliationError(str(error), progress.receipt()) from None
        except Exception:
            if submission_hook is None:
                raise
            raise NativeIdentityReconciliationError("CURRENT_OPERATION_UNCONFIRMED", progress.receipt()) from None

    def _submit(
        self,
        submission: PolicySubmission,
        hook: Callable[[PolicySubmission], DurableSubmissionReceipt],
        progress: _SubmissionProgress,
        checkpoint: Callable[[], None],
    ) -> None:
        self._checkpoint(checkpoint)
        progress.note(
            submission,
            PolicyStepState.UNKNOWN
            if submission.intent.submission_phase == PolicySubmissionPhase.SENT
            else PolicyStepState.UNSENT,
        )
        try:
            receipt = hook(submission)
        except Exception:
            raise NativeIdentityError("SUBMISSION_COMMIT_UNCONFIRMED") from None
        if (
            type(receipt) is not DurableSubmissionReceipt
            or type(receipt.journal_version) is not int
            or receipt.journal_version <= progress.journal_version
            or receipt.submission_id != submission.intent.submission_id
            or receipt.submission_sha256 != submission.submission_sha256
            or receipt.ledger_sha256 != submission.ledger_sha256
            or type(receipt.phase) is not PolicySubmissionPhase
            or receipt.phase != submission.intent.submission_phase
        ):
            raise NativeIdentityError("DURABLE_SUBMISSION_RECEIPT_REQUIRED")
        _guid(receipt.journal_id)
        if progress.journal_id and receipt.journal_id != progress.journal_id:
            raise NativeIdentityError("SUBMISSION_JOURNAL_CHANGED")
        progress.journal_id, progress.journal_version = receipt.journal_id, receipt.journal_version
        progress.ledger = submission.ledger
        progress.note(
            submission,
            PolicyStepState.UNSENT if receipt.phase == PolicySubmissionPhase.UNSENT else PolicyStepState.SENT,
        )

    def _set(
        self,
        fn: Any,
        request: Any,
        checkpoint: Callable[[], None],
        submission: PolicySubmission,
        progress: _SubmissionProgress,
    ) -> Any:
        self._checkpoint(checkpoint)
        progress.note(submission, PolicyStepState.UNKNOWN, invoked=True)
        try:
            value = fn(request=request, retry=None, timeout=TIMEOUT)
        except Exception:
            self._checkpoint(checkpoint)
            raise NativeIdentityError("NATIVE_SET_UNCONFIRMED") from None
        self._checkpoint(checkpoint)
        self._bounded(value)
        return value

    def _desired(
        self, permissions: Iterable[dict[str, str]], *, service_account_uids: tuple[str, ...]
    ) -> tuple[dict[str, set[OwnedGrant]], set[str], str]:
        desired: dict[str, set[OwnedGrant]] = {}
        roles: set[str] = set()
        for index, permission in enumerate(permissions):
            if index >= MAX_RESOURCES - 1 or set(permission) != {"role", "resource"}:
                raise NativeIdentityError("INVALID_PERMISSION_SET")
            resource = self._resource(permission["resource"])
            role = permission["role"]
            if resource == self.context.service_account_resource or not self._role(role):
                raise NativeIdentityError("PREDICTION_CUSTOM_ROLE_REQUIRED")
            roles.add(role)
            desired.setdefault(resource, set()).add(OwnedGrant(role, f"serviceAccount:{self.context.email}"))
        if len(service_account_uids) > 64 or len(set(service_account_uids)) != len(service_account_uids):
            raise NativeIdentityError("INVALID_PRINCIPAL_SET")
        if roles and not service_account_uids:
            raise NativeIdentityError("INVALID_PRINCIPAL_SET")
        desired[self.context.service_account_resource] = {
            OwnedGrant("roles/iam.workloadIdentityUser", self.context.principal(uid)) for uid in service_account_uids
        }
        desired_union_sha256 = _hash(
            (
                self.context.fingerprint,
                tuple(
                    (resource, tuple((grant.role, grant.member) for grant in sorted(grants)))
                    for resource, grants in sorted(desired.items())
                ),
            )
        )
        return desired, roles, desired_union_sha256

    def _reconcile(
        self,
        permissions: Iterable[dict[str, str]],
        *,
        service_account_uids: tuple[str, ...],
        ledger: OwnedGrantLedger,
        checkpoint: Callable[[], None],
        persist: Callable[[OwnedGrantLedger], None],
        submission_hook: Callable[[PolicySubmission], DurableSubmissionReceipt] | None,
        progress: _SubmissionProgress,
    ) -> NativeIdentityResult:
        from google.iam.v1 import iam_policy_pb2

        self._ledger(ledger)
        desired, roles, desired_union_sha256 = self._desired(permissions, service_account_uids=service_account_uids)
        if submission_hook is not None:
            for intent in ledger.pending:
                if not intent.submission_id:
                    raise NativeIdentityError("AMBIGUOUS_LEGACY_SUBMISSION")
                if intent.desired_union_sha256 != desired_union_sha256:
                    raise NativeIdentityError("PENDING_SUBMISSION_UNION_CHANGED")
        self._admit(checkpoint)
        _, iam, endpoints = self._native()
        self._verify_roles(roles, checkpoint)
        resources = (
            set(desired)
            | {row.resource for row in ledger.policies}
            | {row.resource for row in ledger.pending}
            | {row.resource for row in ledger.removals}
        )
        if len(resources) > MAX_RESOURCES:
            raise NativeIdentityError("TOO_MANY_RESOURCES")
        if submission_hook is not None:
            for intent in tuple(ledger.pending):
                if intent.submission_phase != PolicySubmissionPhase.SENT:
                    continue
                client = iam if intent.resource == self.context.service_account_resource else endpoints
                observed = self._call(
                    client.get_iam_policy,
                    iam_policy_pb2.GetIamPolicyRequest(
                        resource=intent.resource, options={"requested_policy_version": 3}
                    ),
                    checkpoint,
                )
                self._policy(observed)
                if self._policy_hash(observed) != intent.after_sha256:
                    raise NativeIdentityError("SENT_SUBMISSION_UNRESOLVED")
                progress.note(PolicySubmission(self.context.fingerprint, intent, ledger), PolicyStepState.OBSERVED)
                ledger = self._record(ledger, intent.resource, set(intent.owned_after))
                persist(ledger)
        for resource in sorted(resources):
            self._admit(checkpoint)
            client = iam if resource == self.context.service_account_resource else endpoints
            if client is endpoints and desired.get(resource):
                endpoint = self._call(endpoints.get_endpoint, {"name": resource}, checkpoint)
                if self._resource(endpoint.name) != resource:
                    raise NativeIdentityError("ENDPOINT_IDENTITY_CHANGED")
            policy = self._call(
                client.get_iam_policy,
                iam_policy_pb2.GetIamPolicyRequest(resource=resource, options={"requested_policy_version": 3}),
                checkpoint,
            )
            self._policy(policy)
            current_hash = self._policy_hash(policy)
            pending = next((row for row in ledger.pending if row.resource == resource), None)
            owned = set(next((row.grants for row in ledger.policies if row.resource == resource), ()))
            if pending:
                if current_hash == pending.after_sha256:
                    if submission_hook is not None and pending.submission_phase == PolicySubmissionPhase.UNSENT:
                        raise NativeIdentityError("UNSENT_POLICY_CONFLICT")
                    owned = set(pending.owned_after)
                    if submission_hook is not None:
                        progress.note(
                            PolicySubmission(self.context.fingerprint, pending, ledger), PolicyStepState.OBSERVED
                        )
                    ledger = self._record(ledger, resource, owned)
                    persist(ledger)
                elif submission_hook is not None and pending.submission_phase == PolicySubmissionPhase.SENT:
                    raise NativeIdentityError("SENT_SUBMISSION_UNRESOLVED")
                elif current_hash != pending.before_sha256:
                    raise NativeIdentityError("PENDING_POLICY_CONFLICT")
                elif submission_hook is not None and hashlib.sha256(policy.etag).hexdigest() != pending.etag_sha256:
                    raise NativeIdentityError("UNSENT_POLICY_ETAG_CHANGED")
            owned.update(next((row.grants for row in ledger.removals if row.resource == resource), ()))
            wanted = desired.get(resource, set())
            removed = owned - wanted
            if removed:
                existing = set(next((row.grants for row in ledger.removals if row.resource == resource), ()))
                ledger = replace(
                    ledger,
                    removals=(
                        *tuple(row for row in ledger.removals if row.resource != resource),
                        PolicyOwnership(resource, tuple(sorted(existing | removed))),
                    ),
                )
            changed = type(policy)()
            changed.CopyFrom(policy)
            for grant in sorted(owned):
                matches = [
                    binding
                    for binding in changed.bindings
                    if not binding.HasField("condition")
                    and binding.role == grant.role
                    and grant.member in binding.members
                ]
                if len(matches) > 1 or any(list(binding.members).count(grant.member) > 1 for binding in matches):
                    raise NativeIdentityError("OWNED_GRANT_AMBIGUOUS")
                if grant not in wanted:
                    for binding in matches:
                        binding.members.remove(grant.member)
                        if not binding.members:
                            changed.bindings.remove(binding)
            owned_after = owned & wanted
            for grant in sorted(wanted):
                matches = [
                    binding
                    for binding in changed.bindings
                    if not binding.HasField("condition")
                    and binding.role == grant.role
                    and grant.member in binding.members
                ]
                if matches:
                    continue
                binding = next(
                    (
                        binding
                        for binding in changed.bindings
                        if not binding.HasField("condition") and binding.role == grant.role
                    ),
                    None,
                )
                if binding is None:
                    binding = changed.bindings.add(role=grant.role)
                binding.members.append(grant.member)
                owned_after.add(grant)
            self._policy(changed)
            after_hash = self._policy_hash(changed)
            if after_hash != current_hash:
                intent = PolicyIntent(resource, current_hash, after_hash, tuple(sorted(owned_after)))
                if submission_hook is not None:
                    intent = replace(
                        intent,
                        submission_id=pending.submission_id if pending else str(uuid4()),
                        submission_phase=PolicySubmissionPhase.UNSENT,
                        etag_sha256=hashlib.sha256(policy.etag).hexdigest(),
                        desired_union_sha256=desired_union_sha256,
                    )
                ledger = replace(
                    ledger, pending=(*tuple(row for row in ledger.pending if row.resource != resource), intent)
                )
                if submission_hook is None:
                    persist(ledger)
                if submission_hook is not None:
                    self._submit(
                        PolicySubmission(self.context.fingerprint, intent, ledger),
                        submission_hook,
                        progress,
                        checkpoint,
                    )
                self._admit(checkpoint)
                self._verify_roles(roles, checkpoint)
                if submission_hook is not None:
                    intent = replace(intent, submission_phase=PolicySubmissionPhase.SENT)
                    ledger = replace(
                        ledger, pending=(*tuple(row for row in ledger.pending if row.resource != resource), intent)
                    )
                    self._submit(
                        PolicySubmission(self.context.fingerprint, intent, ledger),
                        submission_hook,
                        progress,
                        checkpoint,
                    )
                request = iam_policy_pb2.SetIamPolicyRequest(
                    resource=resource, policy=changed, update_mask={"paths": ["bindings", "etag", "version"]}
                )
                if submission_hook is not None:
                    self._set(
                        client.set_iam_policy,
                        request,
                        checkpoint,
                        PolicySubmission(self.context.fingerprint, intent, ledger),
                        progress,
                    )
                else:
                    self._call(client.set_iam_policy, request, checkpoint)
                readback = self._call(
                    client.get_iam_policy,
                    iam_policy_pb2.GetIamPolicyRequest(resource=resource, options={"requested_policy_version": 3}),
                    checkpoint,
                )
                self._policy(readback)
                if self._policy_hash(readback) != after_hash:
                    raise NativeIdentityError("POLICY_READBACK_MISMATCH")
                if submission_hook is not None:
                    progress.note(PolicySubmission(self.context.fingerprint, intent, ledger), PolicyStepState.OBSERVED)
            ledger = self._record(ledger, resource, owned_after)
            persist(ledger)
        self._admit(checkpoint)
        self._verify_roles(roles, checkpoint)
        external = self._verify_union(resources, desired, ledger, checkpoint)
        self._admit(checkpoint)
        self._verify_roles(roles, checkpoint)
        ledger = replace(ledger, removals=())
        persist(ledger)
        return NativeIdentityResult(ledger, {"iam.gke.io/gcp-service-account": self.context.email}, external)

    def _verify_union(
        self,
        resources: set[str],
        desired: dict[str, set[OwnedGrant]],
        ledger: OwnedGrantLedger,
        checkpoint: Callable[[], None],
    ) -> int:
        from google.iam.v1 import iam_policy_pb2

        _, iam, endpoints = self._native()
        external = 0
        for resource in sorted(resources):
            self._admit(checkpoint)
            client = iam if resource == self.context.service_account_resource else endpoints
            wanted = desired.get(resource, set())
            if client is endpoints and wanted:
                endpoint = self._call(endpoints.get_endpoint, {"name": resource}, checkpoint)
                if self._resource(endpoint.name) != resource:
                    raise NativeIdentityError("ENDPOINT_IDENTITY_CHANGED")
            policy = self._call(
                client.get_iam_policy,
                iam_policy_pb2.GetIamPolicyRequest(resource=resource, options={"requested_policy_version": 3}),
                checkpoint,
            )
            self._policy(policy)
            owned = set(next((row.grants for row in ledger.policies if row.resource == resource), ()))
            removed = set(next((row.grants for row in ledger.removals if row.resource == resource), ())) - wanted
            for grant in wanted | removed:
                occurrences = sum(
                    list(binding.members).count(grant.member)
                    for binding in policy.bindings
                    if not binding.HasField("condition") and binding.role == grant.role
                )
                if (grant in removed and occurrences) or (grant in wanted and not occurrences):
                    raise NativeIdentityError("FINAL_POLICY_UNION_MISMATCH")
                if grant in owned and occurrences != 1:
                    raise NativeIdentityError("OWNED_GRANT_AMBIGUOUS")
                if grant in wanted and grant not in owned:
                    external += 1
        return external

    @staticmethod
    def _record(ledger: OwnedGrantLedger, resource: str, owned: set[OwnedGrant]) -> OwnedGrantLedger:
        policies = tuple(row for row in ledger.policies if row.resource != resource)
        if owned:
            policies += (PolicyOwnership(resource, tuple(sorted(owned))),)
        return replace(
            ledger, policies=policies, pending=tuple(row for row in ledger.pending if row.resource != resource)
        )


def desired_owned_union_sha256(
    context: NativeIdentityContext,
    permissions: Iterable[dict[str, str]],
    *,
    service_account_uids: tuple[str, ...],
) -> str:
    """Pure hash of the exact bounded native grant plan used by reconcile.

    Does not discover credentials, construct clients or establish admission.
    """
    return NativeGCPIdentity(context)._desired(permissions, service_account_uids=service_account_uids)[2]


def validate_owned_ledger(context: NativeIdentityContext, ledger: OwnedGrantLedger) -> None:
    """Pure context/resource/grant checks; native ownership still needs readback."""
    NativeGCPIdentity(context)._ledger(ledger)
