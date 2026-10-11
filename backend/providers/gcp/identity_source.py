"""Original GCP source admission and one-shot GSA creation, not workload readiness."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from gcp.gke_identity_observation import _CredentialRequest, _endpoint
from gcp.identity_owned import MAX_BYTES, TIMEOUT, _guid, _hash

if TYPE_CHECKING:
    from collections.abc import Callable

_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_NUMBER = re.compile(r"[1-9][0-9]{0,19}\Z")
_REGION = re.compile(r"[a-z]+(?:-[a-z0-9]+)+[0-9]\Z")
_LOCATION = re.compile(r"[a-z]+(?:-[a-z0-9]+)+[0-9](?:-[a-z])?\Z")
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_UID = re.compile(r"[1-9][0-9]{0,31}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_NATIVE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class IdentitySourceError(ValueError):
    """Fixed reasons only; no credential, native body or diagnostic exposure."""


def checkpoint(fn: Callable[[], object]) -> None:
    if not callable(fn) or fn() is not None:
        raise IdentitySourceError("CURRENT_ADMISSION_UNCONFIRMED")


@dataclass(frozen=True)
class SourceDeclaration:
    organization_id: str
    app_id: str
    cluster_id: str
    project_id: str
    region: str
    location: str
    cluster_name: str
    endpoint: str = field(repr=False)
    certificate_sha256: str
    credential: CloudCredential = field(repr=False)

    def __post_init__(self) -> None:
        for value in (self.organization_id, self.app_id, self.cluster_id):
            _guid(value)
        if (
            (not _PROJECT.fullmatch(self.project_id) and not _NUMBER.fullmatch(self.project_id))
            or not _REGION.fullmatch(self.region)
            or not _LOCATION.fullmatch(self.location)
            or not _NAME.fullmatch(self.cluster_name)
            or not _DIGEST.fullmatch(self.certificate_sha256)
            or self.credential.cloud != "gcp"
            or self.credential.mode != CredentialMode.AMBIENT
            or (
                self.credential.declared_account != self.project_id
                and not _NUMBER.fullmatch(self.credential.declared_account)
            )
            or self.credential.role_arn
            or self.credential.external_id
        ):
            raise IdentitySourceError("INVALID_SOURCE_DECLARATION")
        _endpoint(self.endpoint)

    @property
    def cluster_resource(self) -> str:
        return f"projects/{self.project_id}/locations/{self.location}/clusters/{self.cluster_name}"


@dataclass(frozen=True)
class ProjectScope:
    """Verified pre-GSA scope, deliberately contains no fabricated account UID."""

    organization_id: str
    app_id: str
    cluster_id: str
    project_id: str
    project_number: str
    region: str
    credential: CloudCredential = field(repr=False)

    def __post_init__(self) -> None:
        for value in (self.organization_id, self.app_id, self.cluster_id):
            _guid(value)
        if (
            not _PROJECT.fullmatch(self.project_id)
            or not _NUMBER.fullmatch(self.project_number)
            or not _REGION.fullmatch(self.region)
            or self.credential.cloud != "gcp"
            or self.credential.mode != CredentialMode.AMBIENT
            or self.credential.declared_account not in (self.project_id, self.project_number)
            or self.credential.role_arn
            or self.credential.external_id
        ):
            raise IdentitySourceError("INVALID_PROJECT_SCOPE")

    @property
    def owner_description(self) -> str:
        return "astrolift-owned-identity-v1:" + _hash(
            (self.organization_id, self.app_id, self.cluster_id, self.project_id, self.project_number)
        )


@dataclass(frozen=True)
class ClusterPin:
    project_id: str
    project_number: str
    location: str
    cluster_name: str
    native_cluster_id: str
    workload_pool: str
    endpoint_sha256: str
    certificate_sha256: str

    def __post_init__(self) -> None:
        if (
            not _PROJECT.fullmatch(self.project_id)
            or not _NUMBER.fullmatch(self.project_number)
            or not _LOCATION.fullmatch(self.location)
            or not _NAME.fullmatch(self.cluster_name)
            or not _NATIVE.fullmatch(self.native_cluster_id)
            or self.workload_pool != self.project_id + ".svc.id.goog"
            or any(not _DIGEST.fullmatch(v) for v in (self.endpoint_sha256, self.certificate_sha256))
        ):
            raise IdentitySourceError("INVALID_CLUSTER_PIN")


@dataclass(frozen=True)
class CreateRequest:
    source_id: str
    operation_id: str
    account_id: str
    project_id: str
    project_number: str
    owner_description: str

    def __post_init__(self) -> None:
        _guid(self.source_id)
        _guid(self.operation_id)
        if (
            self.account_id != "astro-" + hashlib.sha256(self.source_id.encode()).hexdigest()[:24]
            or not _PROJECT.fullmatch(self.project_id)
            or not _NUMBER.fullmatch(self.project_number)
            or not re.fullmatch(r"astrolift-owned-identity-v1:[a-f0-9]{64}", self.owner_description)
        ):
            raise IdentitySourceError("INVALID_CREATE_REQUEST")

    @property
    def email(self) -> str:
        return f"{self.account_id}@{self.project_id}.iam.gserviceaccount.com"

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "name": "projects/" + self.project_id,
            "account_id": self.account_id,
            "service_account": {
                "display_name": "astro-source:" + self.source_id + ":" + self.operation_id,
                "description": self.owner_description,
            },
        }

    @property
    def sha256(self) -> str:
        return _hash(self.payload)


@dataclass(frozen=True)
class CreatedAccountEvidence:
    source_id: str
    operation_id: str
    request_sha256: str
    unique_id: str

    def __post_init__(self) -> None:
        _guid(self.source_id)
        _guid(self.operation_id)
        if not _DIGEST.fullmatch(self.request_sha256) or not _UID.fullmatch(self.unique_id):
            raise IdentitySourceError("INVALID_CREATE_EVIDENCE")


@dataclass(frozen=True)
class CreateCommitReceipt:
    source_id: str
    source_version: int
    operation_id: str
    request_sha256: str
    state: str
    unique_id: str | None = None

    def validate(self, request: CreateRequest, state: str) -> None:
        if (
            self.source_id != request.source_id
            or self.operation_id != request.operation_id
            or self.request_sha256 != request.sha256
            or self.state != state
            or type(self.source_version) is not int
            or self.source_version < 1
            or (state == "SENT" and self.unique_id is not None)
            or (state == "EVIDENCE" and (not isinstance(self.unique_id, str) or not _UID.fullmatch(self.unique_id)))
        ):
            raise IdentitySourceError("DURABLE_CREATE_RECEIPT_REQUIRED")


class NativeIdentitySource:
    """Trusted ports use actual native clients; never accepts caller endpoint overrides."""

    def __init__(self, declaration: SourceDeclaration, *, clients: tuple[Any, Any, Any, Any] | None = None):
        if type(declaration) is not SourceDeclaration:
            raise IdentitySourceError("INVALID_SOURCE_DECLARATION")
        self.declaration, self._clients = declaration, clients
        self._owned, self._closed = clients is None, False

    def close(self) -> None:
        if not self._closed and self._owned and self._clients:
            for client in self._clients:
                client.transport.close()
        self._closed = True

    def _native(self, current: Callable[[], None]) -> tuple[Any, Any, Any, Any]:
        checkpoint(current)
        if self._closed:
            raise IdentitySourceError("CLOSED")
        if self._clients is None:
            import google.auth
            import google.auth.transport.grpc
            from google.cloud import aiplatform_v1beta1, container_v1, iam_admin_v1, resourcemanager_v3
            from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.base import (
                DEFAULT_CLIENT_INFO as EP_INFO,
            )
            from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.grpc import (
                EndpointServiceGrpcTransport,
            )
            from google.cloud.container_v1.services.cluster_manager.transports.base import (
                DEFAULT_CLIENT_INFO as GKE_INFO,
            )
            from google.cloud.container_v1.services.cluster_manager.transports.grpc import ClusterManagerGrpcTransport
            from google.cloud.iam_admin_v1.services.iam.transports.base import DEFAULT_CLIENT_INFO as IAM_INFO
            from google.cloud.iam_admin_v1.services.iam.transports.grpc import IAMGrpcTransport
            from google.cloud.resourcemanager_v3.services.projects.transports.base import (
                DEFAULT_CLIENT_INFO as RM_INFO,
            )
            from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

            made: list[Any] = []
            try:
                if os.environ.get("GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES") == "1":
                    raise IdentitySourceError("CREDENTIAL_TYPE_UNSUPPORTED")
                request = _CredentialRequest(current)
                credentials, _ = google.auth.default(
                    request=cast("Any", request), scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                checkpoint(current)
                if type(credentials).__module__ not in (
                    "google.auth.compute_engine.credentials",
                    "google.oauth2.service_account",
                    "google.oauth2.credentials",
                ):
                    raise IdentitySourceError("CREDENTIAL_TYPE_UNSUPPORTED")
                for kind, transport_kind, host, info in (
                    (
                        resourcemanager_v3.ProjectsClient,
                        ProjectsGrpcTransport,
                        "cloudresourcemanager.googleapis.com",
                        RM_INFO,
                    ),
                    (
                        container_v1.ClusterManagerClient,
                        ClusterManagerGrpcTransport,
                        "container.googleapis.com",
                        GKE_INFO,
                    ),
                    (iam_admin_v1.IAMClient, IAMGrpcTransport, "iam.googleapis.com", IAM_INFO),
                    (
                        aiplatform_v1beta1.EndpointServiceClient,
                        EndpointServiceGrpcTransport,
                        f"{self.declaration.region}-aiplatform.googleapis.com",
                        EP_INFO,
                    ),
                ):
                    checkpoint(current)
                    channel_factory: Any = google.auth.transport.grpc.secure_authorized_channel
                    channel = channel_factory(
                        credentials, request, host + ":443", options=[("grpc.max_receive_message_length", MAX_BYTES)]
                    )
                    try:
                        transport: Any = transport_kind(host=host, channel=channel)
                        transport._logged_channel = channel
                        transport._stubs.clear()
                        transport._wrapped_methods.clear()
                        transport._prep_wrapped_messages(info)
                        client_factory: Any = kind
                        made.append(client_factory(transport=transport))
                    except Exception:
                        channel.close()
                        raise
                    checkpoint(current)
            except Exception:
                for client in made:
                    client.transport.close()
                checkpoint(current)
                raise IdentitySourceError("NATIVE_CLIENT_UNAVAILABLE") from None
            self._clients = (made[0], made[1], made[2], made[3])
        return self._clients

    @staticmethod
    def _bounded(value: Any) -> None:
        if len(type(value).serialize(value)) > MAX_BYTES:
            raise IdentitySourceError("OVERSIZED_RESPONSE")

    def _call(self, fn: Any, request: dict[str, Any], current: Callable[[], None], *, absent: bool = False) -> Any:
        from google.api_core.exceptions import NotFound

        checkpoint(current)
        try:
            result = fn(request=request, retry=None, timeout=TIMEOUT)
        except NotFound:
            checkpoint(current)
            if absent:
                return None
            raise IdentitySourceError("NATIVE_SOURCE_UNAVAILABLE") from None
        except Exception:
            checkpoint(current)
            raise IdentitySourceError("NATIVE_SOURCE_UNAVAILABLE") from None
        checkpoint(current)
        self._bounded(result)
        return result

    def observe_cluster(self, *, current: Callable[[], None]) -> tuple[ProjectScope, ClusterPin]:
        rm, gke, _, _ = self._native(current)
        d = self.declaration
        project = self._call(rm.get_project, {"name": "projects/" + d.project_id}, current)
        number = project.name.removeprefix("projects/")
        if (
            project.name != "projects/" + number
            or not _NUMBER.fullmatch(number)
            or not _PROJECT.fullmatch(project.project_id)
            or d.project_id not in (project.project_id, number)
            or d.credential.declared_account not in (project.project_id, number)
            or project.state != 1
        ):
            raise IdentitySourceError("PROJECT_IDENTITY_CHANGED")
        cluster = self._call(gke.get_cluster, {"name": d.cluster_resource}, current)
        if (
            cluster.name != d.cluster_name
            or cluster.location != d.location
            or not _NATIVE.fullmatch(cluster.id)
            or cluster.status != 2
            or cluster.workload_identity_config.workload_pool != project.project_id + ".svc.id.goog"
            or _endpoint(cluster.endpoint) != _endpoint(d.endpoint)
            or hashlib.sha256(cluster.master_auth.cluster_ca_certificate.encode()).hexdigest() != d.certificate_sha256
        ):
            raise IdentitySourceError("REGISTERED_CLUSTER_SOURCE_CHANGED")
        again = self._call(rm.get_project, {"name": "projects/" + number}, current)
        if again.name != project.name or again.project_id != project.project_id or again.state != 1:
            raise IdentitySourceError("PROJECT_IDENTITY_CHANGED")
        return (
            ProjectScope(d.organization_id, d.app_id, d.cluster_id, project.project_id, number, d.region, d.credential),
            ClusterPin(
                project.project_id,
                number,
                d.location,
                d.cluster_name,
                cluster.id,
                cluster.workload_identity_config.workload_pool,
                hashlib.sha256(_endpoint(cluster.endpoint).encode()).hexdigest(),
                d.certificate_sha256,
            ),
        )

    def observe_endpoints(
        self, scope: ProjectScope, snapshot: Any, *, cluster_pin: ClusterPin, current: Callable[[], None]
    ) -> tuple[Any, ...]:
        from gcp.managed._ownership import is_marked_for

        _, _, iam, endpoints = self._native(current)
        if type(cluster_pin) is not ClusterPin:
            raise IdentitySourceError("ORIGINAL_CLUSTER_PIN_REQUIRED")
        if not snapshot.endpoints:
            observed, latest_pin = self.observe_cluster(current=current)
            if observed != scope or latest_pin != cluster_pin:
                raise IdentitySourceError("ORIGINAL_CLUSTER_INCARNATION_CHANGED")
            return ()
        role = self._call(iam.get_role, {"name": snapshot.prediction_role}, current)
        if (
            role.name != snapshot.prediction_role
            or role.deleted
            or role.stage != 2
            or list(role.included_permissions) != ["aiplatform.endpoints.predict"]
        ):
            raise IdentitySourceError("PREDICTION_CUSTOM_ROLE_UNVERIFIED")
        result = []
        for expected in snapshot.endpoints:
            endpoint = self._call(endpoints.get_endpoint, {"name": expected.endpoint}, current)
            if (
                endpoint.name != expected.endpoint
                or not is_marked_for(dict(endpoint.labels), expected.service_guid)
                or len(endpoint.deployed_models) != 1
            ):
                raise IdentitySourceError("ENDPOINT_SOURCE_UNVERIFIED")
            model = endpoint.deployed_models[0]
            resources = model.dedicated_resources
            result.append(
                (
                    expected.endpoint,
                    (
                        model.id,
                        model.model,
                        model.model_version_id,
                        resources.machine_spec.machine_type,
                        resources.min_replica_count,
                        resources.max_replica_count,
                        model.status.available_replica_count,
                        dict(endpoint.traffic_split).get(model.id, 0),
                    ),
                )
            )
        # Mapping and current registered cluster are checked again after all reads.
        observed, latest_pin = self.observe_cluster(current=current)
        if observed != scope or latest_pin != cluster_pin:
            raise IdentitySourceError("PROJECT_IDENTITY_CHANGED")
        return tuple(result)

    def require_absent(self, request: CreateRequest, *, current: Callable[[], None]) -> None:
        _, _, iam, _ = self._native(current)
        if (
            self._call(
                iam.get_service_account,
                {"name": f"projects/{request.project_id}/serviceAccounts/{request.email}"},
                current,
                absent=True,
            )
            is not None
        ):
            raise IdentitySourceError("EXISTING_ACCOUNT_NOT_ADOPTABLE")

    @staticmethod
    def _account(value: Any, request: CreateRequest, *, unique_id: str | None = None) -> str:
        if (
            value.name != f"projects/{request.project_id}/serviceAccounts/{request.email}"
            or value.project_id != request.project_id
            or value.email != request.email
            or not _UID.fullmatch(value.unique_id)
            or value.disabled
            or value.description != request.owner_description
            or value.display_name != request.payload["service_account"]["display_name"]
            or (unique_id is not None and value.unique_id != unique_id)
        ):
            raise IdentitySourceError("ACCOUNT_CREATE_OR_OWNERSHIP_UNCONFIRMED")
        return cast("str", value.unique_id)

    def create(
        self,
        request: CreateRequest,
        *,
        current: Callable[[], None],
        cluster_pin: ClusterPin,
        commit_sent: Callable[[], CreateCommitReceipt],
        retain_evidence: Callable[[CreatedAccountEvidence], CreateCommitReceipt],
    ) -> CreatedAccountEvidence:
        scope, latest_pin = self.observe_cluster(current=current)
        if type(cluster_pin) is not ClusterPin or latest_pin != cluster_pin:
            raise IdentitySourceError("ORIGINAL_CLUSTER_INCARNATION_CHANGED")
        if (
            request.project_id != scope.project_id
            or request.project_number != scope.project_number
            or request.owner_description != scope.owner_description
        ):
            raise IdentitySourceError("CREATE_SOURCE_CHANGED")
        _, _, iam, _ = self._native(current)
        self.require_absent(request, current=current)
        checkpoint(current)
        receipt = commit_sent()
        if type(receipt) is not CreateCommitReceipt:
            raise IdentitySourceError("DURABLE_CREATE_RECEIPT_REQUIRED")
        receipt.validate(request, "SENT")
        checkpoint(current)
        try:
            value = iam.create_service_account(request=request.payload, retry=None, timeout=TIMEOUT)
            self._bounded(value)
            uid = self._account(value, request)
        except Exception:
            # No marker search or retry can prove the immutable UID after an uncertain send.
            raise IdentitySourceError("ACCOUNT_CREATE_UNKNOWN") from None
        evidence = CreatedAccountEvidence(request.source_id, request.operation_id, request.sha256, uid)
        retained = retain_evidence(evidence)
        if type(retained) is not CreateCommitReceipt:
            raise IdentitySourceError("DURABLE_CREATE_EVIDENCE_REQUIRED")
        retained.validate(request, "EVIDENCE")
        if retained.unique_id != evidence.unique_id or retained.source_version <= receipt.source_version:
            raise IdentitySourceError("DURABLE_CREATE_EVIDENCE_REQUIRED")
        checkpoint(current)
        return evidence

    def read_account(self, request: CreateRequest, unique_id: str, *, current: Callable[[], None]) -> None:
        if not _UID.fullmatch(unique_id):
            raise IdentitySourceError("ORIGINAL_ACCOUNT_UID_REQUIRED")
        _, _, iam, _ = self._native(current)
        value = self._call(
            iam.get_service_account, {"name": f"projects/{request.project_id}/serviceAccounts/{unique_id}"}, current
        )
        self._account(value, request, unique_id=unique_id)
