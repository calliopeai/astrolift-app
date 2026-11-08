"""Exact, cluster-filtered public observations from explicit controller metrics."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx
from prometheus_client.parser import parse_labels

from nvke._private_http import private_http
from nvke.client import NvkeError, validate_endpoint_url
from nvke.contract import _contract

MAX_BYTES = 16 * 1024 * 1024
MAX_SAMPLES = 200_000
_SAMPLE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?[ \t]+([^ \t]+)[ \t]*\Z")
_NUMBER = re.compile(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]{1,3})?\Z")
_BOOLEAN = frozenset(
    {
        "certificate_required",
        "certificate_observation_available",
        "certificate_inspection_available",
        "credential_monitoring_enabled",
        "renewal_automation_enabled",
        "credential_metrics_collection_success",
        "renewal_mutation_lock_retained",
        "renewal_state",
        "etcd_key_rotation_state",
    }
)


def catalog() -> dict[str, Any]:
    return copy.deepcopy(_contract()["credential_metrics"])


@dataclass(frozen=True)
class CredentialMetric:
    name: str
    labels: tuple[tuple[str, str], ...]
    value: str


@dataclass(frozen=True)
class CredentialMetricsSnapshot:
    samples: tuple[CredentialMetric, ...]
    cluster_reported: bool


def _invalid() -> NvkeError:
    return NvkeError("INVALID_METRICS", "NVKE credential metrics do not match the pinned observation contract")


def _identity(value: str) -> None:
    try:
        if not value or not value.isprintable() or len(value.encode("utf-8")) > 1000:
            raise _invalid()
    except UnicodeError:
        raise _invalid() from None


def _value(name: str, text: str) -> str:
    if len(text) > 128 or _NUMBER.fullmatch(text) is None:
        raise _invalid()
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _invalid() from None
    if not value.is_finite() or value.adjusted() > 18 or value.adjusted() < -18:
        raise _invalid()
    if name in _BOOLEAN:
        if value not in {0, 1}:
            raise _invalid()
    elif name == "certificate_not_after_timestamp_seconds":
        if value != value.to_integral_value() or not -62135596800 <= value <= 253402300799:
            raise _invalid()
    elif name == "serving_csr_count":
        if value != value.to_integral_value() or not 0 <= value <= 9223372036854775807:
            raise _invalid()
    elif name in {"certificate_observation_age_seconds", "serving_csr_observation_age_seconds"}:
        if value < 0 and value != -1:
            raise _invalid()
    elif value < 0 or (name == "certificate_warning_window_seconds" and value <= 0):
        raise _invalid()
    if value == 0:
        return "0"
    exact = format(value, "f")
    return exact.rstrip("0").rstrip(".") if "." in exact else exact


def project(text: str, cluster_name: str | None) -> CredentialMetricsSnapshot:
    if cluster_name is not None:
        _identity(cluster_name)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise _invalid() from None
    if size > MAX_BYTES:
        raise NvkeError("RESPONSE_TOO_LARGE", "NVKE metrics exceed the observation size limit")
    contract = catalog()
    definitions = {row["name"]: row["labels"] for row in contract["metrics"]}
    credentials = {row["name"]: row["required"] for row in contract["credentials"]}
    prefix = contract["prefix"]
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    result = []
    cluster_reported = False
    collected = False
    declared = set()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# TYPE "):
            parts = line.split()
            metric_name = parts[2] if len(parts) > 2 else ""
            name = metric_name.removeprefix(prefix)
            if metric_name.startswith(prefix) and name in definitions:
                if len(parts) != 4 or parts[3] != "gauge" or name in declared:
                    raise _invalid()
                declared.add(name)
        if not line or line.startswith("#"):
            continue
        metric_name = re.split(r"[\s{]", line, maxsplit=1)[0]
        name = metric_name.removeprefix(prefix)
        if not metric_name.startswith(prefix) or name not in definitions:
            continue
        match = _SAMPLE.fullmatch(line)
        if match is None:
            raise _invalid()
        try:
            labels = parse_labels(match[2] or "", openmetrics=True)
        except ValueError:
            raise _invalid() from None
        if set(labels) != set(definitions[name]):
            raise _invalid()
        for label in ("tenant_cluster", "node"):
            if label in labels:
                _identity(labels[label])
        if "credential" in labels and labels["credential"] not in credentials:
            raise _invalid()
        if "state" in labels and labels["state"] not in contract["states"][name]:
            raise _invalid()
        value = _value(name, match[3])
        if name == "certificate_required" and (value == "1") != credentials[labels["credential"]]:
            raise _invalid()
        key = name, tuple(sorted(labels.items()))
        if key in seen:
            raise _invalid()
        seen.add(key)
        if len(seen) > MAX_SAMPLES:
            raise NvkeError("OBSERVATION_LIMIT", "NVKE metrics exceed the sample inventory limit")
        if name == "credential_metrics_collection_success":
            collected = True
        if "tenant_cluster" in labels:
            if cluster_name is None or labels["tenant_cluster"] != cluster_name:
                continue
            cluster_reported = True
        result.append(
            CredentialMetric(
                name, tuple((key, value) for key, value in sorted(labels.items()) if key != "tenant_cluster"), value
            )
        )
    if not collected:
        raise NvkeError("SURFACE_UNAVAILABLE", "The explicit endpoint does not expose pinned NVKE credential metrics")
    return CredentialMetricsSnapshot(tuple(sorted(result, key=lambda row: (row.name, row.labels))), cluster_reported)


class CredentialMetricsClient:
    def __init__(
        self,
        url: str,
        *,
        allow_http: bool = False,
        token: Callable[[], str] | None = None,
        verify: Any = True,
        timeout: float = 30,
        max_bytes: int = MAX_BYTES,
        before_request: Callable[[], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        validate_endpoint_url(url, allow_http=allow_http)
        if (
            not math.isfinite(timeout)
            or not 0 < timeout <= 300
            or type(max_bytes) is not int
            or not 0 < max_bytes <= MAX_BYTES
        ):
            raise ValueError("Use bounded NVKE metrics timeout and response size")
        self._url, self._token, self._before_request, self._max_bytes = url, token, before_request, max_bytes
        self._timeout = timeout
        self._http = httpx.Client(
            verify=verify, timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def read(self, cluster_name: str | None = None) -> CredentialMetricsSnapshot:
        if cluster_name is not None:
            _identity(cluster_name)
        if self._before_request:
            self._before_request()
        headers = {"Accept": "text/plain; version=0.0.4; charset=utf-8"}
        if self._token:
            try:
                token = self._token()
            except Exception:
                raise NvkeError("CREDENTIALS_UNAVAILABLE", "NVKE metrics credentials could not be loaded") from None
            if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise NvkeError("CREDENTIALS_UNAVAILABLE", "NVKE metrics bearer credential is invalid")
            headers["Authorization"] = f"Bearer {token}"
        with private_http():
            try:
                if self._before_request:
                    self._before_request()
                deadline = monotonic() + self._timeout
                with self._http.stream("GET", self._url, headers=headers) as response:
                    if response.status_code != 200:
                        raise NvkeError(
                            "METRICS_UNAVAILABLE",
                            "The explicit NVKE metrics endpoint could not be read",
                            status_code=response.status_code,
                            retryable=response.status_code in {408, 425, 429} or response.status_code >= 500,
                        )
                    if not response.headers.get("Content-Type", "text/plain").lower().startswith("text/plain"):
                        raise _invalid()
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if monotonic() >= deadline:
                            raise NvkeError("TRANSPORT_ERROR", "NVKE metrics observation timed out", retryable=True)
                        if len(content) + len(chunk) > self._max_bytes:
                            raise NvkeError("RESPONSE_TOO_LARGE", "NVKE metrics exceed the observation size limit")
                        content.extend(chunk)
                if monotonic() >= deadline:
                    raise NvkeError("TRANSPORT_ERROR", "NVKE metrics observation timed out", retryable=True)
                try:
                    text = content.decode("utf-8")
                except UnicodeError:
                    raise _invalid() from None
                result = project(text, cluster_name)
                if self._before_request:
                    self._before_request()
                return result
            except httpx.HTTPError:
                raise NvkeError(
                    "TRANSPORT_ERROR", "NVKE metrics connection failed or timed out", retryable=True
                ) from None
