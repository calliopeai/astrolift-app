# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded management-Kubernetes provider inspection with no Secret value reads.

The elapsed deadline is checked on raw body chunks and request boundaries. Socket
waits use the remaining budget at dispatch; already-blocking connect/header I/O
can finish later, but no late observation is returned. Native free-form fields
remain private while the fixed projection preserves independently observed facts.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import httpx

from nvke.client import _VERSION, Endpoint, NvkeClient, NvkeError
from nvke.contract import Operation, upstream_revision
from nvke.infrastructure import (
    _NAMESPACE,
    GROUP,
    CredentialHealth,
    Identity,
    Provider,
    _generation,
    _name,
    _object,
    assess_credentials,
    decode_identity,
    providers,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

MAX_OBSERVATION_BYTES = 32 * 1024 * 1024
MAX_PAGES = 100
MAX_ROWS = 10_000
PAGE_SIZE = 100
METADATA_ACCEPT = "application/json;as=PartialObjectMetadata;g=meta.k8s.io;v=v1"
State = Literal["NOT-INSTALLED", "NOT-CONFIGURED", "DEGRADED", "CONFIGURED", "UNKNOWN"]


class _GuardedStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, check: Callable[[], None]) -> None:
        self.stream, self.check = stream, check

    def __iter__(self) -> Iterator[bytes]:
        iterator = iter(self.stream)
        while True:
            self.check()
            try:
                chunk = next(iterator)
            except StopIteration:
                return
            self.check()
            yield chunk

    def close(self) -> None:
        self.stream.close()


class _GuardedTransport(httpx.BaseTransport):
    def __init__(self, transport: httpx.BaseTransport, check: Callable[[], None]) -> None:
        self.transport, self.check = transport, check

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.transport.handle_request(request)
        if not isinstance(response.stream, httpx.SyncByteStream):
            raise NvkeError("INVALID_RESPONSE", "Management Kubernetes returned an invalid response stream")
        response.stream = _GuardedStream(response.stream, self.check)
        return response

    def close(self) -> None:
        self.transport.close()


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    state: State
    reason: str
    complete: bool = True
    crds_established: bool | None = None
    controller_available: bool | None = None
    manager_namespace: str | None = None
    manager_name: str | None = None
    ready_replicas: int | None = None
    desired_replicas: int | None = None
    secret_namespace: str | None = None
    secret_name: str | None = None
    secret_present: bool | None = None
    endpoint_configured: bool | None = None
    unconfigured_is_valid: bool = False
    credentials: CredentialHealth | None = None


@dataclass(frozen=True)
class ProviderObservation:
    upstream_revision: str
    observed_at: str
    complete: bool
    providers: tuple[ProviderStatus, ...]


@dataclass(frozen=True)
class _Manager:
    namespace: str
    name: str
    app: str
    available: bool
    ready: int | None
    replicas: int | None
    args: tuple[str, ...] = field(repr=False)


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 9223372036854775807 else None


def _manager(document: dict[str, Any]) -> _Manager | None:
    metadata, spec, status = (_object(document.get(k)) for k in ("metadata", "spec", "status"))
    namespace, name = _name(metadata.get("namespace")), _name(metadata.get("name"))
    if not namespace or _NAMESPACE.fullmatch(namespace) is None or not name:
        return None
    generation = _generation(metadata.get("generation"))
    observed = _generation(status.get("observedGeneration"))
    conditions = status.get("conditions")
    available = generation is not None and observed is not None and observed >= generation
    available = (
        available
        and metadata.get("deletionTimestamp") is None
        and isinstance(conditions, list)
        and any(isinstance(c, dict) and c.get("type") == "Available" and c.get("status") == "True" for c in conditions)
    )
    containers = _object(_object(spec.get("template")).get("spec")).get("containers")
    args = (
        tuple(
            a
            for c in containers
            if isinstance(c, dict) and isinstance(c.get("args"), list)
            for a in c["args"]
            if isinstance(a, str)
        )
        if isinstance(containers, list)
        else ()
    )
    app = _object(metadata.get("labels")).get("app.kubernetes.io/name")
    return _Manager(
        namespace,
        name,
        app if isinstance(app, str) else "",
        available,
        _count(status.get("readyReplicas")),
        _count(status.get("replicas")),
        args,
    )


def _flag(manager: _Manager, flag: str) -> str | None:
    if not flag:
        return None
    for index, value in enumerate(manager.args):
        if value.startswith(flag + "="):
            return value[len(flag) + 1 :]
        if value == flag and index + 1 < len(manager.args):
            return manager.args[index + 1]
    return None


class InfrastructureClient(NvkeClient):
    def __init__(self, endpoint: Endpoint, *, before_request: Callable[[], None] | None = None, **kwargs: Any) -> None:
        if endpoint.surface != "datastore":
            raise ValueError("Provider inspection requires the explicit management-Kubernetes connection")
        self._checkpoint = before_request
        self._deadline: float | None = None
        self._inspection_lock = threading.Lock()
        self._endpoint = endpoint
        timeout = float(kwargs.pop("timeout", 30.0))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Provider inspection timeout must be positive and finite")
        self._socket_timeout = min(timeout, 30.0)
        self._observed_bytes = 0
        transport = kwargs.pop("transport", None)
        if transport is None:
            transport = httpx.HTTPTransport(verify=kwargs.get("verify", True), trust_env=False, retries=0)
        super().__init__(
            [endpoint], timeout=self._socket_timeout, transport=_GuardedTransport(transport, self._time_check), **kwargs
        )

    def _time_check(self) -> None:
        if self._deadline is None or time.monotonic() >= self._deadline:
            raise NvkeError("OBSERVATION_TIMEOUT", "Provider inspection deadline expired")

    def _check(self) -> None:
        self._time_check()
        if self._checkpoint:
            self._checkpoint()

    def _fetch(
        self, path: str, *, query: dict[str, Any] | None = None, metadata: bool = False
    ) -> tuple[dict[str, Any] | None, str | None]:
        self._check()
        assert self._deadline is not None
        self._http.timeout = httpx.Timeout(max(0.001, min(self._socket_timeout, self._deadline - time.monotonic())))
        try:
            response = self._call(
                Operation("infrastructure.providers", "datastore", "GET", path),
                self._endpoint,
                path={},
                query=query or {},
                body=None,
                headers={"Accept": METADATA_ACCEPT} if metadata else None,
            )
            data = response.data
            error = None if isinstance(data, dict) else "INVALID_RESPONSE"
        except NvkeError as exc:
            if exc.code == "OBSERVATION_TIMEOUT":
                raise
            data, error = None, "FORBIDDEN" if exc.code == "PERMISSION_DENIED" else exc.code
        self._check()
        if isinstance(data, dict):
            self._observed_bytes += len(json.dumps(data, separators=(",", ":")).encode())
            if self._observed_bytes > MAX_OBSERVATION_BYTES:
                raise NvkeError("OBSERVATION_LIMIT", "Provider inspection exceeds the total response limit")
        return (data if isinstance(data, dict) else None), error

    def _list(
        self, path: str, *, labels: str = "", namespace: str | None = None
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        rows: list[dict[str, Any]] = []
        cursors, keys = set(), set()
        cursor, version = "", None
        for _page in range(MAX_PAGES):
            query: dict[str, Any] = {"limit": PAGE_SIZE}
            if labels:
                query["labelSelector"] = labels
            if cursor:
                query["continue"] = cursor
            data, error = self._fetch(path, query=query)
            if error or data is None:
                return None, error or "INVALID_RESPONSE"
            metadata, items = data.get("metadata"), data.get("items")
            if not isinstance(metadata, dict) or not isinstance(items, list):
                return None, "INVALID_RESPONSE"
            current = metadata.get("resourceVersion")
            if (
                not isinstance(current, str)
                or len(current) > 16384
                or _VERSION.fullmatch(current) is None
                or (version is not None and current != version)
            ):
                return None, "COLLECTION_CHANGED"
            version = current
            for item in items:
                if not isinstance(item, dict):
                    return None, "INVALID_RESPONSE"
                meta = _object(item.get("metadata"))
                key = (meta.get("namespace", ""), meta.get("name"))
                if (
                    not all(isinstance(k, str) for k in key)
                    or _name(key[1]) is None
                    or _NAMESPACE.fullmatch(key[0]) is None
                    or (namespace is not None and key[0] != namespace)
                    or key in keys
                ):
                    return None, "INVALID_RESPONSE"
                keys.add(key)
                rows.append(item)
                if len(rows) > MAX_ROWS:
                    return None, "OBSERVATION_LIMIT"
            cursor = metadata.get("continue", "")
            if not isinstance(cursor, str) or len(cursor) > 16384 or any(ord(c) < 33 or ord(c) > 126 for c in cursor):
                return None, "INVALID_RESPONSE"
            if not cursor:
                return rows, None
            if cursor in cursors:
                return None, "COLLECTION_CHANGED"
            cursors.add(cursor)
        return None, "OBSERVATION_LIMIT"

    def _established(self, name: str) -> tuple[bool | None, str | None]:
        data, error = self._fetch("/apis/apiextensions.k8s.io/v1/customresourcedefinitions/" + name)
        if error == "NOT_FOUND":
            return False, None
        if error or data is None:
            return None, error or "INVALID_RESPONSE"
        if _object(data.get("metadata")).get("name") != name:
            return None, "INVALID_RESPONSE"
        if _object(data.get("metadata")).get("deletionTimestamp") is not None:
            return False, None
        versions, conditions = _object(data.get("spec")).get("versions"), _object(data.get("status")).get("conditions")
        if conditions is None:
            conditions = []
        if not isinstance(versions, list) or not isinstance(conditions, list):
            return None, "INVALID_RESPONSE"
        return any(isinstance(v, dict) and v.get("served") is True for v in versions) and any(
            isinstance(c, dict) and c.get("type") == "Established" and c.get("status") == "True" for c in conditions
        ), None

    def _one(
        self, definition: Provider, managers: list[_Manager] | None, manager_error: str | None, now_ns: int
    ) -> ProviderStatus:
        if managers is None or manager_error:
            return ProviderStatus(
                definition.type,
                "UNKNOWN",
                "provider_manager_inventory_unreadable",
                False,
                unconfigured_is_valid=definition.unconfigured_is_valid,
            )
        available = [self._established(name) for name in definition.crds]
        if any(error for _ready, error in available):
            return ProviderStatus(
                definition.type,
                "UNKNOWN",
                "provider_crd_inventory_unreadable",
                False,
                unconfigured_is_valid=definition.unconfigured_is_valid,
            )
        if not all(ready for ready, _error in available):
            return ProviderStatus(
                definition.type,
                "NOT-INSTALLED",
                "provider_crds_not_established",
                crds_established=False,
                unconfigured_is_valid=definition.unconfigured_is_valid,
            )
        manager = next((m for m in managers if m.app == definition.app_name), None) or next(
            (
                m
                for m in managers
                if (m.namespace, m.name) == (definition.controller_namespace, definition.controller_deployment)
            ),
            None,
        )
        namespace = _flag(manager, definition.secret_namespace_flag) if manager else None
        namespace = (
            namespace if namespace is not None else manager.namespace if manager else definition.controller_namespace
        )
        secret = _flag(manager, definition.secret_name_flag) if manager else None
        secret = secret if secret is not None else definition.default_secret_name
        if not _NAMESPACE.fullmatch(namespace) or (secret and _name(secret) is None):
            return ProviderStatus(
                definition.type,
                "UNKNOWN",
                "provider_secret_reference_invalid",
                False,
                crds_established=True,
                unconfigured_is_valid=definition.unconfigured_is_valid,
            )
        state: State
        present, error = None, None
        endpoint = bool(_flag(manager, definition.endpoint_flag)) if manager and definition.endpoint_flag else None
        if not secret:
            state, reason = "NOT-CONFIGURED", "provider_secret_reference_absent"
        else:
            data, error = self._fetch(f"/api/v1/namespaces/{namespace}/secrets/{secret}", metadata=True)
            if error == "NOT_FOUND":
                present, error = False, None
            elif error is None:
                meta = _object((data or {}).get("metadata"))
                if (
                    data is None
                    or data.get("kind") != "PartialObjectMetadata"
                    or data.get("apiVersion") != "meta.k8s.io/v1"
                    or "data" in data
                    or "stringData" in data
                    or (meta.get("namespace"), meta.get("name")) != (namespace, secret)
                ):
                    error = "INVALID_RESPONSE"
                else:
                    present = True
            if error:
                state, reason = "UNKNOWN", "provider_secret_presence_unreadable"
            elif not present:
                state, reason = "NOT-CONFIGURED", "provider_secret_absent"
            elif manager and endpoint is False:
                state, reason = "DEGRADED", "provider_endpoint_absent"
            elif manager is None:
                state, reason = "DEGRADED", "provider_manager_not_found"
            elif not manager.available:
                state, reason = "DEGRADED", "provider_manager_unavailable"
            else:
                state, reason = "CONFIGURED", "provider_configuration_present"
        credentials = None
        complete = error is None
        if definition.identity_resource:
            established, identity_error = self._established(definition.identity_resource + "." + GROUP)
            identities: list[Identity] = []
            if established:
                documents, identity_error = self._list(
                    f"/apis/{GROUP}/{definition.identity_version}/namespaces/{namespace}/{definition.identity_resource}",
                    namespace=namespace,
                )
                if documents is not None:
                    for document in documents:
                        identity = decode_identity(document)
                        if identity is not None:
                            identities.append(identity)
            complete = complete and identity_error is None
            credentials = assess_credentials(
                tuple(identities),
                crd_established=established is not False,
                secret_namespace=namespace,
                secret_name=secret,
                now_ns=now_ns,
                list_error=identity_error,
                manager_found=manager is not None,
                manager_available=bool(
                    manager and manager.available and manager.ready is not None and manager.ready > 0
                ),
                watch_namespace=(_flag(manager, definition.watch_namespace_flag) or "") if manager else "",
                watch_filter=(_flag(manager, definition.watch_filter_flag) or "") if manager else "",
            )
        return ProviderStatus(
            provider=definition.type,
            state=state,
            reason=reason,
            complete=complete,
            crds_established=True,
            controller_available=manager.available if manager else False,
            manager_namespace=manager.namespace if manager else None,
            manager_name=manager.name if manager else None,
            ready_replicas=manager.ready if manager else None,
            desired_replicas=manager.replicas if manager else None,
            secret_namespace=namespace,
            secret_name=secret or None,
            secret_present=present,
            endpoint_configured=endpoint,
            unconfigured_is_valid=definition.unconfigured_is_valid,
            credentials=credentials,
        )

    def inspect(self) -> ProviderObservation:
        if not self._inspection_lock.acquire(blocking=False):
            raise NvkeError("OBSERVATION_BUSY", "Provider inspection is already running")
        try:
            definitions = providers()
            self._deadline = time.monotonic() + 30.0
            self._observed_bytes = 0
            documents, error = self._list("/apis/apps/v1/deployments", labels="app.kubernetes.io/name")
            managers = (
                [manager for d in documents if (manager := _manager(d)) is not None] if documents is not None else None
            )
            now_ns = time.time_ns()
            statuses = tuple(self._one(definition, managers, error, now_ns) for definition in definitions)
            self._check()
            seconds, nanos = divmod(now_ns, 1_000_000_000)
            observed = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}Z"
            return ProviderObservation(
                upstream_revision(), observed, error is None and all(s.complete for s in statuses), statuses
            )
        finally:
            self._deadline = None
            self._inspection_lock.release()
