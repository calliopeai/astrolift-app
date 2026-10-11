"""Bounded native Vertex metadata; no adoption, deployment or invoke authority."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from _sdk.cloud_credentials import CloudCredential, CredentialMode

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

MAX_ITEMS = 500
MAX_PAGES = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DEPLOYED_MODELS = 64
MAX_VERSION_ALIASES = 32
TIMEOUT = 10.0
_PROJECT_ID = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_NUMBER = re.compile(r"[1-9][0-9]{0,19}\Z")
_REGION = re.compile(r"[a-z]+(?:-[a-z0-9]+)+[0-9]\Z")
_RESOURCE = re.compile(r"projects/([^/]+)/locations/([^/]+)/(endpoints|models)/([a-zA-Z0-9_-]{1,128})\Z")
_VERSION = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
_DEPLOYMENT_ID = re.compile(r"[0-9]{1,10}\Z")


class CatalogueState(StrEnum):
    METADATA = "metadata"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    ERROR = "error"
    REFUSED = "refused"


class VertexCatalogueError(ValueError):
    """Fixed reasons only, without provider bodies or credential material."""


@dataclass(frozen=True)
class VertexCatalogueConfig:
    project: str
    region: str
    credential: CloudCredential = field(repr=False)


@dataclass(frozen=True)
class VertexCatalogueIdentity:
    project_id: str
    project_number: str
    region: str


@dataclass(frozen=True)
class VertexDeploymentMetadata:
    id: str
    model: str
    model_version_id: str | None
    machine_type: str | None
    min_replicas: int | None
    max_replicas: int | None
    available_replicas: int
    traffic_percent: int


@dataclass(frozen=True)
class VertexCatalogueSource:
    kind: str
    name: str
    display_name: str
    version_id: str | None = None
    version_aliases: tuple[str, ...] = ()
    deployments: tuple[VertexDeploymentMetadata, ...] = ()
    # Native metadata/version IDs are observations, not an adopted immutable pin.
    invoke_access: str = "unknown"


@dataclass(frozen=True)
class VertexCatalogueResult:
    state: CatalogueState
    identity: VertexCatalogueIdentity | None = None
    items: tuple[VertexCatalogueSource, ...] = ()
    truncated: bool = False
    reason: str | None = None


class VertexCatalogue:
    """Admit the current organization/provider source before constructing.

    Production privately shares one ADC credential across fixed-host native
    clients. Injected clients are a trusted, caller-owned native-test port.
    Project metadata proves a resource mapping, never caller/tenant identity.
    """

    def __init__(
        self,
        config: VertexCatalogueConfig,
        *,
        clients: tuple[Any, Any, Any] | None = None,
        checkpoint: Callable[[], object] | None = None,
    ) -> None:
        credential = config.credential
        if (
            not (_PROJECT_ID.fullmatch(config.project) or _NUMBER.fullmatch(config.project))
            or not _REGION.fullmatch(config.region)
            or credential.cloud != "gcp"
            or credential.mode != CredentialMode.AMBIENT
            or credential.declared_account != config.project
            or credential.role_arn
            or credential.external_id
            or (checkpoint is not None and not callable(checkpoint))
        ):
            raise VertexCatalogueError("INVALID_CONFIGURATION")
        self.config = config
        self._clients = clients
        self._owned = clients is None
        self._identity: VertexCatalogueIdentity | None = None
        self._closed = False
        self._checkpoint = checkpoint

    def _admit(self) -> None:
        if self._checkpoint is not None:
            try:
                if self._checkpoint() is not None:
                    raise VertexCatalogueError("CURRENT_SOURCE_UNAVAILABLE")
            except Exception:
                raise VertexCatalogueError("CURRENT_SOURCE_UNAVAILABLE") from None

    def _read(self, method: Callable[..., Any], request: dict[str, Any]) -> Any:
        self._admit()
        result = method(request=request, retry=None, timeout=TIMEOUT)
        self._admit()
        return result

    def __enter__(self) -> VertexCatalogue:
        if self._closed:
            raise VertexCatalogueError("CLOSED")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed and self._owned and self._clients:
            for client in self._clients:
                client.transport.close()
        self._closed = True

    def endpoints(self, *, limit: int = 100, max_pages: int = MAX_PAGES) -> VertexCatalogueResult:
        return self._list("endpoints", limit, max_pages)

    def models(self, *, limit: int = 100, max_pages: int = MAX_PAGES) -> VertexCatalogueResult:
        return self._list("models", limit, max_pages)

    def endpoint_detail(self, name: str) -> VertexCatalogueResult:
        return self._detail("endpoints", name)

    def model_detail(self, name: str) -> VertexCatalogueResult:
        # Alias/version suffixes are deliberately not caller inputs. GetModel
        # without a suffix observes the current default; it does not pin it.
        return self._detail("models", name)

    def _native(self) -> tuple[Any, Any, Any]:
        if self._closed:
            raise VertexCatalogueError("CLOSED")
        self._admit()
        if self._clients is None:
            import google.auth
            from google.cloud import aiplatform_v1, resourcemanager_v3
            from google.cloud.aiplatform_v1.services.endpoint_service.transports.base import (
                DEFAULT_CLIENT_INFO as ENDPOINT_CLIENT_INFO,
            )
            from google.cloud.aiplatform_v1.services.endpoint_service.transports.grpc import (
                EndpointServiceGrpcTransport,
            )
            from google.cloud.aiplatform_v1.services.model_service.transports.base import (
                DEFAULT_CLIENT_INFO as MODEL_CLIENT_INFO,
            )
            from google.cloud.aiplatform_v1.services.model_service.transports.grpc import ModelServiceGrpcTransport
            from google.cloud.resourcemanager_v3.services.projects.transports.base import (
                DEFAULT_CLIENT_INFO as PROJECT_CLIENT_INFO,
            )
            from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

            credential, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"])
            self._admit()
            made: list[Any] = []
            factories: tuple[tuple[Any, Any, str, Any], ...] = (
                (
                    resourcemanager_v3.ProjectsClient,
                    ProjectsGrpcTransport,
                    "cloudresourcemanager.googleapis.com",
                    PROJECT_CLIENT_INFO,
                ),
                (
                    aiplatform_v1.EndpointServiceClient,
                    EndpointServiceGrpcTransport,
                    f"{self.config.region}-aiplatform.googleapis.com",
                    ENDPOINT_CLIENT_INFO,
                ),
                (
                    aiplatform_v1.ModelServiceClient,
                    ModelServiceGrpcTransport,
                    f"{self.config.region}-aiplatform.googleapis.com",
                    MODEL_CLIENT_INFO,
                ),
            )
            try:
                for client_type, transport_type, host, client_info in factories:
                    self._admit()
                    channel = transport_type.create_channel(
                        host,
                        credentials=credential,
                        options=[("grpc.max_receive_message_length", MAX_RESPONSE_BYTES)],
                    )
                    try:
                        transport = transport_type(host=host, channel=channel)
                        # Generated DEBUG interceptors serialize complete native
                        # payloads. Rebuild only these private transports on the
                        # same authenticated channel, retaining GAPIC wrappers.
                        transport._logged_channel = channel
                        transport._stubs.clear()
                        transport._wrapped_methods.clear()
                        transport._prep_wrapped_messages(client_info)
                        made.append(client_type(transport=transport))
                    except Exception:
                        channel.close()
                        raise
                    self._admit()
            except Exception:
                for client in made:
                    client.transport.close()
                raise
            self._clients = (made[0], made[1], made[2])
        return self._clients

    def _project(self) -> VertexCatalogueIdentity:
        projects, _, _ = self._native()
        project = self._read(projects.get_project, {"name": f"projects/{self.config.project}"})
        self._bounded(project)
        match = re.fullmatch(r"projects/([1-9][0-9]{0,19})", project.name)
        if not match or not _PROJECT_ID.fullmatch(project.project_id) or project.state != 1:
            raise VertexCatalogueError("UNVERIFIED_PROJECT")
        identity = VertexCatalogueIdentity(project.project_id, match[1], self.config.region)
        if self.config.project not in (identity.project_id, identity.project_number):
            raise VertexCatalogueError("UNVERIFIED_PROJECT")
        if self._identity is not None and identity != self._identity:
            raise VertexCatalogueError("PROJECT_MAPPING_CHANGED")
        self._identity = identity
        return identity

    def _resource(self, name: str, kind: str, identity: VertexCatalogueIdentity | None) -> str:
        match = _RESOURCE.fullmatch(name)
        if (
            not match
            or match[2] != self.config.region
            or match[3] != kind
            or (identity and match[1] not in (identity.project_id, identity.project_number))
            or (not identity and not (_PROJECT_ID.fullmatch(match[1]) or _NUMBER.fullmatch(match[1])))
        ):
            raise VertexCatalogueError("INVALID_RESOURCE_IDENTITY")
        return match[4]

    def _list(self, kind: str, limit: int, max_pages: int) -> VertexCatalogueResult:
        if (
            type(limit) is not int
            or not 1 <= limit <= MAX_ITEMS
            or type(max_pages) is not int
            or not 1 <= max_pages <= MAX_PAGES
        ):
            raise VertexCatalogueError("INVALID_LIMIT")
        try:
            self._project()
            items: list[VertexCatalogueSource] = []
            names: set[str] = set()
            tokens: set[str] = set()
            token = ""
            for _ in range(max_pages):
                identity = self._project()
                _, endpoints, models = self._native()
                client = endpoints if kind == "endpoints" else models
                method = client.list_endpoints if kind == "endpoints" else client.list_models
                size = min(100, limit - len(items))
                pager = self._read(
                    method,
                    {
                        "parent": f"projects/{identity.project_number}/locations/{self.config.region}",
                        "page_size": size,
                        "page_token": token,
                        "read_mask": {"paths": self._mask(kind)},
                    },
                )
                # Take exactly one generated page; iterating rows would secretly
                # fetch subsequent pages outside the identity/limit boundary.
                response = next(iter(pager.pages))
                self._bounded(response)
                self._project()
                rows = response.endpoints if kind == "endpoints" else response.models
                if len(rows) > size:
                    raise VertexCatalogueError("INVALID_PAGE")
                for row in rows:
                    source = self._source(row, kind, identity)
                    canonical = self._resource(source.name, kind, identity)
                    if canonical in names:
                        raise VertexCatalogueError("DUPLICATE_RESOURCE")
                    names.add(canonical)
                    items.append(source)
                token = response.next_page_token
                if not token:
                    return VertexCatalogueResult(CatalogueState.METADATA, identity, tuple(items))
                if len(token.encode()) > 4096 or any(ord(c) < 32 for c in token) or token in tokens:
                    raise VertexCatalogueError("INVALID_CONTINUATION")
                tokens.add(token)
                if len(items) == limit:
                    break
            return VertexCatalogueResult(CatalogueState.METADATA, self._identity, tuple(items), truncated=True)
        except Exception as exc:
            return self._failure(exc)

    def _detail(self, kind: str, name: str) -> VertexCatalogueResult:
        try:
            # Reject malformed targets before lookup, then admit either spelling
            # only against the verified native mapping before the Vertex read.
            self._resource(name, kind, self._identity)
            identity = self._project()
            self._resource(name, kind, identity)
            _, endpoints, models = self._native()
            method = endpoints.get_endpoint if kind == "endpoints" else models.get_model
            row = self._read(method, {"name": name})
            self._bounded(row)
            self._project()
            source = self._source(row, kind, identity)
            if self._resource(source.name, kind, identity) != self._resource(name, kind, identity):
                raise VertexCatalogueError("INVALID_RESOURCE_IDENTITY")
            return VertexCatalogueResult(CatalogueState.METADATA, identity, (source,))
        except Exception as exc:
            return self._failure(exc)

    @staticmethod
    def _bounded(response: Any) -> None:
        if type(response).pb(response).ByteSize() > MAX_RESPONSE_BYTES:
            raise VertexCatalogueError("RESPONSE_TOO_LARGE")

    @staticmethod
    def _text(value: str) -> str:
        if len(value.encode()) > 256 or any(ord(c) < 32 for c in value):
            raise VertexCatalogueError("INVALID_METADATA")
        return value

    @staticmethod
    def _mask(kind: str) -> list[str]:
        return (
            ["name", "display_name", "deployed_models", "traffic_split"]
            if kind == "endpoints"
            else ["name", "display_name", "version_id", "version_aliases"]
        )

    def _source(self, row: Any, kind: str, identity: VertexCatalogueIdentity) -> VertexCatalogueSource:
        self._resource(row.name, kind, identity)
        display = self._text(row.display_name)
        if kind == "models":
            if len(row.version_aliases) > MAX_VERSION_ALIASES or len(row.deployed_models) > MAX_DEPLOYED_MODELS:
                raise VertexCatalogueError("METADATA_ARRAY_TOO_LARGE")
            aliases = tuple(self._text(alias) for alias in row.version_aliases)
            version = self._text(row.version_id) or None
            return VertexCatalogueSource("registered_model", row.name, display, version, aliases)
        if len(row.deployed_models) > MAX_DEPLOYED_MODELS or len(row.traffic_split) > MAX_DEPLOYED_MODELS:
            raise VertexCatalogueError("METADATA_ARRAY_TOO_LARGE")
        deployments: list[VertexDeploymentMetadata] = []
        ids: set[str] = set()
        for deployed in row.deployed_models:
            if not _DEPLOYMENT_ID.fullmatch(deployed.id) or deployed.id in ids:
                raise VertexCatalogueError("INVALID_DEPLOYMENT")
            ids.add(deployed.id)
            model_parts = deployed.model.split("@")
            if len(model_parts) > 2 or (len(model_parts) == 2 and not _VERSION.fullmatch(model_parts[1])):
                raise VertexCatalogueError("INVALID_RESOURCE_IDENTITY")
            self._resource(model_parts[0], "models", identity)
            version = self._text(deployed.model_version_id) or None
            resources = deployed.dedicated_resources
            dedicated = type(deployed).pb(deployed).WhichOneof("prediction_resources") == "dedicated_resources"
            deployments.append(
                VertexDeploymentMetadata(
                    deployed.id,
                    deployed.model,
                    version,
                    self._text(resources.machine_spec.machine_type) or None,
                    resources.min_replica_count if dedicated else None,
                    resources.max_replica_count if dedicated else None,
                    deployed.status.available_replica_count,
                    row.traffic_split.get(deployed.id, 0),
                )
            )
        if any(key not in ids or not 0 <= value <= 100 for key, value in row.traffic_split.items()) or sum(
            row.traffic_split.values()
        ) not in (0, 100):
            raise VertexCatalogueError("INVALID_TRAFFIC")
        return VertexCatalogueSource("endpoint", row.name, display, deployments=tuple(deployments))

    @staticmethod
    def _failure(exc: Exception) -> VertexCatalogueResult:
        from google.api_core.exceptions import NotFound, PermissionDenied, Unauthenticated

        if isinstance(exc, VertexCatalogueError):
            return VertexCatalogueResult(CatalogueState.REFUSED, reason=str(exc))
        if isinstance(exc, (PermissionDenied, Unauthenticated)):
            return VertexCatalogueResult(CatalogueState.DENIED, reason="NATIVE_READ_DENIED")
        if isinstance(exc, NotFound):
            return VertexCatalogueResult(CatalogueState.NOT_FOUND, reason="NATIVE_RESOURCE_NOT_FOUND")
        return VertexCatalogueResult(CatalogueState.ERROR, reason="NATIVE_READ_UNAVAILABLE")
