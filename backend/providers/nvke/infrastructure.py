# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pinned provider registry and secret-safe provider-reported credential health.

Semantics follow NVKE's Apache-2.0 cli/internal/providers implementation. Source
hashes bind this projection to the bundled snapshot; native messages stay private.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from nvke.client import NvkeError
from nvke.contract import _contract

GROUP = "infrastructure.cluster.x-k8s.io"
SOURCE_HASHES = {
    "cli/internal/providers/providers.go": "e35a026a25f29d92be5c1509a050fb52097dc42565c290d2b6b7debeb96afdb3",
    "cli/internal/providers/inspect/inspect.go": "5e4601b0f6ce73b0a03c38d9f9f3eaf9913471dfa4a717690efbc6561caecb34",
    "cli/internal/providers/credentials/assess.go": "7e01b5995737a333f4e527a8e6f27e7590148d010cc75685d0e636d719f88d3b",
    "cli/internal/providers/credentials/identity.go": (
        "f3c6b0be20e73de3fa34d6f19d6d9c50c4c81b79b9ec1e5be949a50c705f6200"
    ),
    "cli/internal/providers/credentials/credentials.go": (
        "13301a808c5e52ddebf668335c8ac5a2065d02abd268f1735f49b8606bb256b8"
    ),
}
STALE_AFTER_NS = 600_000_000_000
NEXT_CHECK_GRACE_NS = 300_000_000_000
FUTURE_TOLERANCE_NS = 60_000_000_000
Health = Literal["HEALTHY", "FAILING", "UNKNOWN", "STALE", "PENDING", "NO-IDENTITY", "UNSUPPORTED", "UNREADABLE"]
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
_NAMESPACE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,9}))?(?:Z|[+-]\d{2}:\d{2})\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Provider:
    type: str
    crds: tuple[str, ...]
    app_name: str
    controller_namespace: str
    controller_deployment: str
    secret_name_flag: str
    secret_namespace_flag: str
    default_secret_name: str
    endpoint_flag: str = ""
    unconfigured_is_valid: bool = False
    identity_resource: str = ""
    identity_version: str = ""
    watch_namespace_flag: str = ""
    watch_filter_flag: str = ""


def providers() -> tuple[Provider, ...]:
    if any(_contract()["sources"].get(path) != digest for path, digest in SOURCE_HASHES.items()):
        raise NvkeError("UPSTREAM_MISMATCH", "Provider inspection requires review of the pinned native sources")
    return (
        Provider(
            "nico",
            (f"nicoclusters.{GROUP}", f"nicomachinetemplates.{GROUP}"),
            "cluster-api-provider-nico",
            "capnico-system",
            "controller-manager",
            "--provider-credentials-secret-name",
            "--provider-credentials-namespace",
            "nico-credentials",
            identity_resource="nicoidentities",
            identity_version="v1alpha1",
            watch_namespace_flag="--namespace",
            watch_filter_flag="--watch-filter",
        ),
        Provider(
            "launchlayer",
            (f"launchlayerclusters.{GROUP}", f"launchlayermachinetemplates.{GROUP}"),
            "cluster-api-provider-launchlayer",
            "capll-system",
            "cluster-api-provider-launchlayer-controller-manager",
            "--identity-secret",
            "--identity-secret-namespace",
            "",
            endpoint_flag="--base-url",
            unconfigured_is_valid=True,
        ),
    )


@dataclass(frozen=True)
class Timestamp:
    text: str
    nanoseconds: int


def timestamp(value: Any) -> Timestamp | None:
    if not isinstance(value, str) or (match := _DATE.fullmatch(value)) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OverflowError):
        return None
    delta = parsed.replace(microsecond=0) - _EPOCH
    nanos = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + int((match[1] or "0").ljust(9, "0"))
    return Timestamp(value, nanos)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _name(value: Any) -> str | None:
    return value if isinstance(value, str) and _NAME.fullmatch(value) else None


def _generation(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 9223372036854775807 else None


@dataclass(frozen=True)
class Identity:
    namespace: str
    name: str
    generation: int | None
    secret_name: str | None
    created: Timestamp | None
    deleting: bool
    watch_filter: str | None = field(repr=False)
    ready_status: str | None = None
    ready_generation: int | None = None
    last_checked: Timestamp | None = None
    last_checked_invalid: bool = False
    next_check: Timestamp | None = None


def decode_identity(document: dict[str, Any]) -> Identity | None:
    metadata, spec, status = (_object(document.get(key)) for key in ("metadata", "spec", "status"))
    namespace, name = _name(metadata.get("namespace")), _name(metadata.get("name"))
    if not namespace or _NAMESPACE.fullmatch(namespace) is None or not name:
        return None
    conditions = status.get("conditions")
    ready = (
        next((c for c in conditions if isinstance(c, dict) and c.get("type") == "Ready"), {})
        if isinstance(conditions, list)
        else {}
    )
    last = status.get("lastCheckedTime")
    checked = timestamp(last)
    watch = _object(metadata.get("labels")).get("cluster.x-k8s.io/watch-filter")
    ready_status = ready.get("status")
    return Identity(
        namespace=namespace,
        name=name,
        generation=_generation(metadata.get("generation")),
        secret_name=_name(_object(spec.get("credentialsRef")).get("name")),
        created=timestamp(metadata.get("creationTimestamp")),
        deleting=metadata.get("deletionTimestamp") is not None,
        watch_filter=watch if isinstance(watch, str) else None,
        ready_status=ready_status
        if isinstance(ready_status, str) and ready_status in {"True", "False", "Unknown"}
        else None,
        ready_generation=_generation(ready.get("observedGeneration")),
        last_checked=checked,
        last_checked_invalid=last is not None and checked is None,
        next_check=timestamp(status.get("nextCheckTime")),
    )


@dataclass(frozen=True)
class CredentialHealth:
    health: Health
    reason: str
    identity_namespace: str | None = None
    identity_name: str | None = None
    identity_generation: int | None = None
    ready_status: str | None = None
    observed_generation: int | None = None
    last_checked_time: str | None = None
    next_check_time: str | None = None
    additional_identities: tuple[tuple[str, str], ...] = ()


def _current(identity: Identity) -> bool:
    return (
        identity.generation is not None
        and identity.ready_generation == identity.generation
        and identity.last_checked is not None
    )


def _due(identity: Identity) -> int | None:
    if identity.next_check and identity.last_checked:
        gap = identity.next_check.nanoseconds - identity.last_checked.nanoseconds
        if 0 < gap <= STALE_AFTER_NS:
            return identity.next_check.nanoseconds
    return None


def _stale(identity: Identity, now_ns: int) -> bool:
    due = _due(identity)
    if due is not None:
        return now_ns - due > NEXT_CHECK_GRACE_NS
    evidence = identity.last_checked or identity.created
    return evidence is None or now_ns - evidence.nanoseconds > STALE_AFTER_NS


def assess_credentials(
    identities: tuple[Identity, ...],
    *,
    crd_established: bool,
    secret_namespace: str,
    secret_name: str,
    now_ns: int,
    list_error: str | None = None,
    manager_found: bool = False,
    manager_available: bool = False,
    watch_namespace: str = "",
    watch_filter: str = "",
) -> CredentialHealth:
    if not crd_established or list_error == "NOT_FOUND":
        return CredentialHealth("UNSUPPORTED", "identity_api_not_served")
    if list_error:
        return CredentialHealth(
            "UNREADABLE", "identity_list_forbidden" if list_error == "FORBIDDEN" else "identity_list_unavailable"
        )
    candidates = [
        i for i in identities if not i.deleting and i.namespace == secret_namespace and i.secret_name == secret_name
    ]
    if not candidates:
        return CredentialHealth("NO-IDENTITY", "no_identity_observes_secret")
    candidates.sort(
        key=lambda i: (
            not (_current(i) and not _stale(i, now_ns)),
            -(i.last_checked.nanoseconds if i.last_checked else -(10**30)),
            i.name,
        )
    )
    pick = candidates[0]
    health: Health
    if pick.last_checked_invalid:
        health, reason = "UNKNOWN", "invalid_last_checked_time"
    elif pick.last_checked and pick.last_checked.nanoseconds - now_ns > FUTURE_TOLERANCE_NS:
        health, reason = "UNKNOWN", "last_checked_time_in_future"
    elif _stale(pick, now_ns):
        health, reason = "STALE", "provider_check_overdue"
        if not manager_found:
            reason = "provider_manager_not_found"
        elif watch_namespace and watch_namespace != pick.namespace:
            reason = "identity_outside_manager_namespace"
        elif watch_filter and pick.watch_filter != watch_filter:
            reason = "identity_outside_manager_filter"
        elif not manager_available:
            reason = "provider_manager_unavailable"
    elif not _current(pick):
        health, reason = "PENDING", "current_generation_not_checked"
    elif pick.ready_status == "True":
        health, reason = "HEALTHY", "provider_check_succeeded"
    elif pick.ready_status == "False":
        health, reason = "FAILING", "provider_check_failed"
    else:
        health, reason = "UNKNOWN", "provider_check_unknown"
    return CredentialHealth(
        health,
        reason,
        pick.namespace,
        pick.name,
        pick.generation,
        pick.ready_status,
        pick.ready_generation,
        pick.last_checked.text if pick.last_checked else None,
        pick.next_check.text if pick.next_check else None,
        tuple((i.namespace, i.name) for i in candidates[1:]),
    )
