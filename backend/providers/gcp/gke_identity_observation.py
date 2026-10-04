"""Read-only GKE/KSA configuration observations, never identity adoption or rollout."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from gcp.identity_owned import MAX_BYTES, TIMEOUT, NativeIdentityContext, _guid

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_POOLS = 64
MAX_SUBJECTS = 64
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_LOCATION = re.compile(r"[a-z]+(?:-[a-z0-9]+)+[0-9](?:-[a-z])?\Z")
_NATIVE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_VERSION = re.compile(r"[!-~]{1,256}\Z")


class GKEObservationError(ValueError):
    """Fixed private-transport errors; never propagate native exception text."""


def _checkpoint(fn: Callable[[], object]) -> None:
    if fn() is not None:
        raise GKEObservationError("CURRENT_ADMISSION_UNCONFIRMED")


@dataclass(frozen=True)
class KSASubject:
    namespace: str
    name: str
    namespace_uid: str
    service_account_uid: str

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.namespace) or not _NAME.fullmatch(self.name):
            raise GKEObservationError("INVALID_SUBJECT")
        for value in (self.namespace_uid, self.service_account_uid):
            if value:
                _guid(value)


@dataclass(frozen=True)
class GKEObservationContext:
    identity: NativeIdentityContext
    location: str
    cluster_name: str
    native_cluster_id: str
    subjects: tuple[KSASubject, ...]

    def __post_init__(self) -> None:
        if (
            not _LOCATION.fullmatch(self.location)
            or not _NAME.fullmatch(self.cluster_name)
            or not _NATIVE_ID.fullmatch(self.native_cluster_id)
            or not 1 <= len(self.subjects) <= MAX_SUBJECTS
            or len({(row.namespace, row.name) for row in self.subjects}) != len(self.subjects)
        ):
            raise GKEObservationError("INVALID_CONTEXT")

    @property
    def cluster_resource(self) -> str:
        return f"projects/{self.identity.project_id}/locations/{self.location}/clusters/{self.cluster_name}"

    @property
    def owner_labels(self) -> dict[str, str]:
        return {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/organization-id": self.identity.organization_id,
            "astrolift.io/app-id": self.identity.app_id,
            "astrolift.io/cluster-id": self.identity.cluster_id,
        }


@dataclass(frozen=True)
class KSAObservation:
    namespace: str
    name: str
    namespace_uid: str
    service_account_uid: str
    namespace_resource_version: str
    service_account_resource_version: str
    annotation_matches: bool


@dataclass(frozen=True)
class GKEObservation:
    configuration_observed: bool
    reason: str
    native_cluster_id: str = ""
    autopilot: bool = False
    node_pools: tuple[str, ...] = ()
    subjects: tuple[KSAObservation, ...] = ()
    workload_ready: bool = False
    impersonation_verified: bool = False


@dataclass(frozen=True)
class _Response:
    status: int
    data: bytes = field(repr=False)
    headers: dict[str, str] = field(repr=False)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise GKEObservationError("REDIRECT_REFUSED")


def _http(url: str, *, method: str, headers: dict[str, str], data: bytes | None, context: ssl.SSLContext) -> _Response:
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=context))
    try:
        response = opener.open(Request(url, data=data, headers=headers, method=method), timeout=TIMEOUT)
    except HTTPError as error:
        response = error
    try:
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise GKEObservationError("ENCODED_RESPONSE_REFUSED")
        chunks = []
        size = 0
        deadline = time.monotonic() + TIMEOUT
        while True:
            if time.monotonic() >= deadline:
                raise GKEObservationError("READ_DEADLINE_EXCEEDED")
            chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise GKEObservationError("OVERSIZED_RESPONSE")
        raw = b"".join(chunks)
        return _Response(response.code, raw, {key.lower(): value for key, value in response.headers.items()})
    finally:
        response.close()


class _CredentialRequest:
    """No ambient proxy/redirect or arbitrary external-account URL transport."""

    def __init__(self, checkpoint: Callable[[], None]) -> None:
        self.checkpoint = checkpoint

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> _Response:
        _checkpoint(self.checkpoint)
        parsed = urlsplit(url)
        metadata = (
            parsed.scheme == "http"
            and parsed.hostname in ("169.254.169.254", "metadata.google.internal")
            and (parsed.path == "/" or parsed.path.startswith("/computeMetadata/v1/"))
            and method == "GET"
        )
        public = parsed.scheme == "https" and (
            (parsed.hostname == "oauth2.googleapis.com" and parsed.path == "/token")
            or (parsed.hostname == "sts.googleapis.com" and parsed.path == "/v1/token")
            or (
                parsed.hostname == "iamcredentials.googleapis.com"
                and parsed.path.startswith("/v1/projects/-/serviceAccounts/")
                and parsed.path.endswith(":generateAccessToken")
            )
        )
        if (
            not (metadata or public)
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port is not None
            or method not in ("GET", "POST")
        ):
            raise GKEObservationError("CREDENTIAL_ROUTE_UNSUPPORTED")
        data = body.encode() if isinstance(body, str) else body
        if data and len(data) > 65536:
            raise GKEObservationError("OVERSIZED_CREDENTIAL_REQUEST")
        try:
            value = _http(url, method=method, headers=headers or {}, data=data, context=ssl.create_default_context())
        except Exception:
            _checkpoint(self.checkpoint)
            raise GKEObservationError("CREDENTIAL_READ_UNCONFIRMED") from None
        _checkpoint(self.checkpoint)
        return value


def _endpoint(value: str) -> str:
    if len(value) > 253 or "/" in value or ":" in value:
        raise GKEObservationError("ENDPOINT_UNVERIFIED")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        if not re.fullmatch(r"[a-z0-9.-]+\.gke\.goog", value):
            raise GKEObservationError("ENDPOINT_UNVERIFIED") from None
    else:
        private = any(
            address in network
            for network in (
                ipaddress.IPv4Network("10.0.0.0/8"),
                ipaddress.IPv4Network("172.16.0.0/12"),
                ipaddress.IPv4Network("192.168.0.0/16"),
            )
        )
        if (
            not (address.is_global or private)
            or address.is_multicast
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
        ):
            raise GKEObservationError("ENDPOINT_UNVERIFIED")
    return f"https://{value}"


class KubernetesReadAdapter:
    """Fresh native endpoint/CA and the same admitted ADC; exact GETs only."""

    def __init__(self, endpoint: str, certificate: str, credentials: Any, checkpoint: Callable[[], None]) -> None:
        self.base_url = _endpoint(endpoint)
        try:
            pem = base64.b64decode(certificate, validate=True)
            if len(pem) > 65536 or not pem.startswith(b"-----BEGIN CERTIFICATE-----"):
                raise ValueError
            self.tls = ssl.create_default_context(cadata=pem.decode("ascii"))
        except Exception:
            raise GKEObservationError("CA_UNVERIFIED") from None
        self.credentials = credentials
        self.checkpoint = checkpoint
        self.credential_request = _CredentialRequest(checkpoint)

    def read(self, namespace: str, name: str | None = None) -> dict[str, Any]:
        if not _NAME.fullmatch(namespace) or (name is not None and not _NAME.fullmatch(name)):
            raise GKEObservationError("INVALID_SUBJECT")
        path = f"/api/v1/namespaces/{namespace}"
        if name is not None:
            path += f"/serviceaccounts/{name}"
        _checkpoint(self.checkpoint)
        try:
            if not self.credentials.valid:
                self.credentials.refresh(self.credential_request)
            _checkpoint(self.checkpoint)
            token = self.credentials.token
            if not isinstance(token, str) or not token or len(token) > 16384 or any(c in token for c in "\r\n"):
                raise GKEObservationError("CREDENTIAL_UNAVAILABLE")
            response = _http(
                self.base_url + path,
                method="GET",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                data=None,
                context=self.tls,
            )
        except Exception:
            _checkpoint(self.checkpoint)
            raise GKEObservationError("KUBERNETES_READ_UNCONFIRMED") from None
        _checkpoint(self.checkpoint)
        if response.status == 404:
            raise GKEObservationError("SUBJECT_UNOBSERVED")
        if response.status != 200:
            raise GKEObservationError("KUBERNETES_READ_REFUSED")
        try:
            value = json.loads(response.data)
        except Exception:
            raise GKEObservationError("KUBERNETES_RESPONSE_INVALID") from None
        if not isinstance(value, dict):
            raise GKEObservationError("KUBERNETES_RESPONSE_INVALID")
        return value


class GKEIdentityObserver:
    """Injected SDK clients/adapter are trusted test ports, never API input."""

    def __init__(
        self,
        context: GKEObservationContext,
        *,
        clients: tuple[Any, Any] | None = None,
        kubernetes_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.context = context
        self._clients = clients
        self._credentials: Any = None
        self._owned = clients is None
        self._closed = False
        self._kubernetes_factory = kubernetes_factory or KubernetesReadAdapter

    def close(self) -> None:
        if not self._closed and self._owned and self._clients:
            for client in self._clients:
                client.transport.close()
        self._closed = True

    def __enter__(self) -> GKEIdentityObserver:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _native(self, checkpoint: Callable[[], None]) -> tuple[Any, Any]:
        _checkpoint(checkpoint)
        if self._closed:
            raise GKEObservationError("CLOSED")
        if self._clients is None:
            import google.auth
            import google.auth.transport.grpc
            from google.cloud import container_v1, resourcemanager_v3
            from google.cloud.container_v1.services.cluster_manager.transports.base import (
                DEFAULT_CLIENT_INFO as CLUSTER_INFO,
            )
            from google.cloud.container_v1.services.cluster_manager.transports.grpc import ClusterManagerGrpcTransport
            from google.cloud.resourcemanager_v3.services.projects.transports.base import (
                DEFAULT_CLIENT_INFO as PROJECT_INFO,
            )
            from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

            if os.environ.get("GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES") == "1":
                raise GKEObservationError("CREDENTIAL_TYPE_UNSUPPORTED")
            credential_request = _CredentialRequest(checkpoint)
            try:
                credentials, _ = google.auth.default(
                    request=cast("Any", credential_request), scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            except Exception:
                _checkpoint(checkpoint)
                raise GKEObservationError("CREDENTIAL_DISCOVERY_UNCONFIRMED") from None
            _checkpoint(checkpoint)
            if type(credentials).__module__ not in (
                "google.auth.compute_engine.credentials",
                "google.oauth2.service_account",
                "google.oauth2.credentials",
            ):
                raise GKEObservationError("CREDENTIAL_TYPE_UNSUPPORTED")
            self._credentials = credentials
            made: list[Any] = []
            try:
                for client_type, transport_type, host, info in (
                    (
                        resourcemanager_v3.ProjectsClient,
                        ProjectsGrpcTransport,
                        "cloudresourcemanager.googleapis.com",
                        PROJECT_INFO,
                    ),
                    (
                        container_v1.ClusterManagerClient,
                        ClusterManagerGrpcTransport,
                        "container.googleapis.com",
                        CLUSTER_INFO,
                    ),
                ):
                    _checkpoint(checkpoint)
                    channel_factory: Any = google.auth.transport.grpc.secure_authorized_channel
                    channel = channel_factory(
                        credentials,
                        credential_request,
                        host + ":443",
                        options=[("grpc.max_receive_message_length", MAX_BYTES)],
                    )
                    try:
                        transport = transport_type(host=host, channel=channel)
                        transport._logged_channel = channel
                        transport._stubs.clear()
                        transport._wrapped_methods.clear()
                        transport._prep_wrapped_messages(info)
                        made.append(client_type(transport=transport))
                    except Exception:
                        channel.close()
                        raise
                    _checkpoint(checkpoint)
            except Exception:
                for client in made:
                    client.transport.close()
                _checkpoint(checkpoint)
                raise GKEObservationError("NATIVE_CLIENT_UNAVAILABLE") from None
            self._clients = (made[0], made[1])
        return self._clients

    @staticmethod
    def _call(fn: Any, request: dict[str, str], checkpoint: Callable[[], None]) -> Any:
        _checkpoint(checkpoint)
        try:
            value = fn(request=request, retry=None, timeout=TIMEOUT)
        except Exception:
            _checkpoint(checkpoint)
            raise GKEObservationError("NATIVE_READ_UNCONFIRMED") from None
        _checkpoint(checkpoint)
        if len(type(value).serialize(value)) > MAX_BYTES:
            raise GKEObservationError("OVERSIZED_RESPONSE")
        return value

    def _cluster(self, checkpoint: Callable[[], None]) -> tuple[Any, tuple[str, ...]]:
        project_client, cluster_client = self._native(checkpoint)
        context = self.context
        project = self._call(
            project_client.get_project, {"name": f"projects/{context.identity.project_number}"}, checkpoint
        )
        if (
            project.name != f"projects/{context.identity.project_number}"
            or project.project_id != context.identity.project_id
            or project.state != 1
        ):
            raise GKEObservationError("PROJECT_IDENTITY_CHANGED")
        cluster = self._call(cluster_client.get_cluster, {"name": context.cluster_resource}, checkpoint)
        if (
            cluster.id != context.native_cluster_id
            or cluster.name != context.cluster_name
            or cluster.location != context.location
            or cluster.status != 2
            or cluster.workload_identity_config.workload_pool != context.identity.project_id + ".svc.id.goog"
        ):
            raise GKEObservationError("CLUSTER_CONFIGURATION_UNVERIFIED")
        if cluster.autopilot.enabled:
            return cluster, ()
        response = self._call(cluster_client.list_node_pools, {"parent": context.cluster_resource}, checkpoint)
        pools = tuple(response.node_pools)
        if (
            not 1 <= len(pools) <= MAX_POOLS
            or len({pool.name for pool in pools}) != len(pools)
            or any(
                not _NAME.fullmatch(pool.name) or pool.status != 2 or pool.config.workload_metadata_config.mode != 2
                for pool in pools
            )
        ):
            raise GKEObservationError("NODE_CONFIGURATION_UNVERIFIED")
        return cluster, tuple(sorted(pool.name for pool in pools))

    def _metadata(self, value: dict[str, Any], subject: KSASubject, *, namespace: bool) -> dict[str, Any]:
        metadata = value.get("metadata")
        if not isinstance(metadata, dict):
            raise GKEObservationError("SUBJECT_RESPONSE_INVALID")
        expected_uid = subject.namespace_uid if namespace else subject.service_account_uid
        if (
            value.get("apiVersion") != "v1"
            or value.get("kind") != ("Namespace" if namespace else "ServiceAccount")
            or metadata.get("name") != (subject.namespace if namespace else subject.name)
            or (not namespace and metadata.get("namespace") != subject.namespace)
            or metadata.get("uid") != expected_uid
            or metadata.get("deletionTimestamp")
            or not isinstance(metadata.get("resourceVersion"), str)
            or not _VERSION.fullmatch(metadata["resourceVersion"])
            or not isinstance(metadata.get("labels"), dict)
            or any(metadata["labels"].get(key) != value for key, value in self.context.owner_labels.items())
        ):
            raise GKEObservationError("SUBJECT_OWNERSHIP_UNVERIFIED")
        return metadata

    def _subjects(self, adapter: Any, checkpoint: Callable[[], None]) -> tuple[KSAObservation, ...]:
        namespaces: dict[str, dict[str, Any]] = {}
        rows = []
        for subject in self.context.subjects:
            _checkpoint(checkpoint)
            if subject.namespace not in namespaces:
                namespaces[subject.namespace] = adapter.read(subject.namespace)
                _checkpoint(checkpoint)
            namespace = self._metadata(namespaces[subject.namespace], subject, namespace=True)
            _checkpoint(checkpoint)
            account_value = adapter.read(subject.namespace, subject.name)
            _checkpoint(checkpoint)
            account = self._metadata(account_value, subject, namespace=False)
            annotations = account.get("annotations", {})
            if not isinstance(annotations, dict):
                raise GKEObservationError("SUBJECT_RESPONSE_INVALID")
            linked = annotations.get("iam.gke.io/gcp-service-account", "")
            if linked not in ("", self.context.identity.email):
                raise GKEObservationError("SERVICE_ACCOUNT_LINK_FOREIGN")
            rows.append(
                KSAObservation(
                    subject.namespace,
                    subject.name,
                    namespace["uid"],
                    account["uid"],
                    namespace["resourceVersion"],
                    account["resourceVersion"],
                    linked == self.context.identity.email,
                )
            )
        return tuple(rows)

    def observe(self, *, checkpoint: Callable[[], None]) -> GKEObservation:
        _checkpoint(checkpoint)
        if any(not row.namespace_uid or not row.service_account_uid for row in self.context.subjects):
            return GKEObservation(False, "SUBJECT_UID_UNRECORDED")
        try:
            cluster, pools = self._cluster(checkpoint)
            adapter = self._kubernetes_factory(
                cluster.endpoint, cluster.master_auth.cluster_ca_certificate, self._credentials, checkpoint
            )
            rows = self._subjects(adapter, checkpoint)
            final_cluster, final_pools = self._cluster(checkpoint)
            if (
                final_cluster.endpoint != cluster.endpoint
                or final_cluster.master_auth.cluster_ca_certificate != cluster.master_auth.cluster_ca_certificate
                or final_cluster.autopilot.enabled != cluster.autopilot.enabled
                or final_pools != pools
            ):
                raise GKEObservationError("CLUSTER_OBSERVATION_CHANGED")
            final_rows = self._subjects(adapter, checkpoint)
            if final_rows != rows:
                raise GKEObservationError("SUBJECT_OBSERVATION_CHANGED")
            _checkpoint(checkpoint)
            return GKEObservation(True, "CONFIGURATION_OBSERVED", cluster.id, cluster.autopilot.enabled, pools, rows)
        except GKEObservationError as error:
            _checkpoint(checkpoint)
            return GKEObservation(False, str(error))
