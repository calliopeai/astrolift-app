"""Bound, private mTLS client for a site's native operator execution agent."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import ssl
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlsplit

import httpx

from nvke._operator_install_review import project as project_install_review
from nvke._private_http import private_http
from nvke.client import NvkeError, validate_endpoint_url
from nvke.contract import upstream_revision
from nvke.operator_contract import (
    CATALOG,
    MAX_CHUNK_BYTES,
    MAX_REQUEST_BYTES,
    REVISION,
    ContractError,
    identifier,
    validate,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"0|[1-9][0-9]{0,18}\Z")
_STATES = {"preparing", "queued", "dispatching", "running", "completed", "failed", "cancelled", "uncertain"}


def _invalid_response() -> NvkeError:
    return NvkeError("OPERATOR_INVALID_RESPONSE", "The operator agent did not confirm the reviewed identity or receipt")


def verify_contract() -> None:
    try:
        root = Path(__file__).parent
        provenance = json.loads((root / "operator_provenance.json").read_bytes())
        if (
            provenance["format"] != 1
            or provenance["repository"] != "calliopeai/astrolift-opscode"
            or len(provenance["revision"]) != 40
            or hashlib.sha256((root / "operator_contract.py").read_bytes()).hexdigest() != provenance["sha256"]
            or upstream_revision() != REVISION
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise NvkeError("CONTRACT_UNSUPPORTED", "The operator contract requires a source provenance review") from None


class OperatorAgentClient:
    def __init__(
        self,
        endpoint: str,
        *,
        site_id: str,
        verify: ssl.SSLContext,
        instance_id: str | None = None,
        bundle_manifest_sha256: str | None = None,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        verify_contract()
        try:
            validate_endpoint_url(endpoint)
            parsed = urlsplit(endpoint)
            if parsed.path not in {"", "/"} or parsed.scheme != "https":
                raise ValueError
            self.site_id = identifier(site_id)
            self.instance_id = identifier(instance_id) if instance_id is not None else None
            if (instance_id is None) != (bundle_manifest_sha256 is None) or (
                bundle_manifest_sha256 is not None and not _SHA.fullmatch(bundle_manifest_sha256)
            ):
                raise ValueError
            if (
                not isinstance(verify, ssl.SSLContext)
                or not verify.check_hostname
                or verify.verify_mode != ssl.CERT_REQUIRED
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise NvkeError(
                "INVALID_INPUT", "Use explicit HTTPS/mTLS operator authority with its reviewed instance and bundle pin"
            ) from None
        self.bundle_digest = bundle_manifest_sha256
        self._checkpoint = before_request
        self._http = httpx.Client(
            base_url=endpoint.rstrip("/"),
            verify=verify,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        intent: str | None = None,
    ) -> dict[str, Any]:
        if self._checkpoint:
            self._checkpoint()
        data = bytearray()
        headers = {"X-Astro-Nvke-Agent-Instance": self.instance_id} if self.instance_id else {}
        if intent is not None:
            if not isinstance(intent, str) or not _SHA.fullmatch(intent):
                raise NvkeError("INVALID_INPUT", "Use the original opaque operator intent digest")
            headers["X-Astro-Nvke-Intent"] = intent
        try:
            with private_http(), self._http.stream(method, path, json=body, params=params, headers=headers) as response:
                status = response.status_code
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_REQUEST_BYTES:
                        raise _invalid_response()
        except httpx.HTTPError:
            raise NvkeError(
                "OPERATOR_UNAVAILABLE",
                "Original operator intent requires receipt inspection; no native replay is authorized",
                ambiguous=method != "GET",
                retryable=method == "GET",
            ) from None
        finally:
            if self._checkpoint:
                self._checkpoint()
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError):
            raise _invalid_response() from None
        if not isinstance(result, dict):
            raise _invalid_response()
        if status not in {200, 202}:
            peer = result.get("errorCode")
            code = (
                {
                    "JOB_NOT_FOUND": "OPERATOR_JOB_NOT_FOUND",
                    "AGENT_BUSY": "OPERATOR_BUSY",
                    "AGENT_INSTANCE_CHANGED": "SOURCE_CHANGED",
                    "TRUSTED_BUNDLE_CHANGED": "SOURCE_CHANGED",
                    "JOB_CONFLICT": "OPERATOR_JOB_CONFLICT",
                    "ORIGINAL_JOB_FENCED": "OPERATOR_JOB_FENCED",
                    "LEGACY_ADMISSION_HISTORY_INCOMPLETE": "OPERATOR_HISTORY_INCOMPLETE",
                    "JOB_ADMISSION_INCOMPLETE": "OPERATOR_ADMISSION_INCOMPLETE",
                    "ARTIFACT_CHANGED": "SOURCE_CHANGED",
                    "SOURCE_ARTIFACT_CHANGED": "SOURCE_CHANGED",
                }.get(peer, "OPERATOR_REQUEST_REFUSED")
                if isinstance(peer, str)
                else "OPERATOR_REQUEST_REFUSED"
            )
            raise NvkeError(
                code,
                "The operator agent refused the reviewed request; retain its original receipt",
                status_code=status,
                retryable=code == "OPERATOR_BUSY",
                ambiguous=status >= 500 and code != "OPERATOR_BUSY",
            )
        return result

    def identity(self) -> dict[str, Any]:
        data = self._request("GET", "/v1/identity")
        try:
            if (
                type(data["format"]) is not int
                or data["format"] != 1
                or type(data.get("jobFormat")) is not int
                or data["jobFormat"] != 2
                or data["revision"] != REVISION
                or identifier(data["siteId"]) != self.site_id
                or not _SHA.fullmatch(data["bundleManifestSha256"])
                or data["commands"] != CATALOG
            ):
                raise ValueError
            instance = identifier(data["instanceId"])
            if self.instance_id is not None and (
                instance != self.instance_id or data["bundleManifestSha256"] != self.bundle_digest
            ):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise _invalid_response() from None
        return data

    def _bound(self) -> None:
        if self.instance_id is None or self.bundle_digest is None:
            raise NvkeError("PRECONDITION", "Bind the reviewed operator instance before accessing jobs")

    def _receipt(self, data: dict[str, Any], job_id: str, command: str, intent: str) -> dict[str, Any]:
        allowed = {
            "format",
            "revision",
            "siteId",
            "instanceId",
            "id",
            "intent",
            "command",
            "state",
            "effectful",
            "artifacts",
            "cancellationRequested",
            "errorCode",
            "nativeExitCode",
            "reconciledFromNativeReceipt",
        }
        try:
            if (
                data.keys() - allowed
                or type(data["format"]) is not int
                or data["format"] != 2
                or data["intent"] != intent
                or data["revision"] != REVISION
                or data["siteId"] != self.site_id
                or data["instanceId"] != self.instance_id
                or data["id"] != job_id
                or data["command"] != command
                or command not in CATALOG
                or data["state"] not in _STATES
                or type(data["effectful"]) is not bool
                or type(data["cancellationRequested"]) is not bool
            ):
                raise ValueError
            if not isinstance(data["artifacts"], list) or len(data["artifacts"]) > 2048:
                raise ValueError
            names = set()
            for item in data["artifacts"]:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"name", "size", "sha256"}
                    or not isinstance(item["name"], str)
                    or not 1 <= len(item["name"]) <= 4096
                    or item["name"] in names
                    or not isinstance(item["size"], str)
                    or not _DECIMAL.fullmatch(item["size"])
                    or not _SHA.fullmatch(item["sha256"])
                ):
                    raise ValueError
                names.add(item["name"])
            if "nativeExitCode" in data and type(data["nativeExitCode"]) is not int:
                raise ValueError
            if "reconciledFromNativeReceipt" in data and type(data["reconciledFromNativeReceipt"]) is not bool:
                raise ValueError
            if "errorCode" in data and (
                not isinstance(data["errorCode"], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", data["errorCode"])
            ):
                raise ValueError
            if data["state"] == "completed" and data.get("nativeExitCode") != 0:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise _invalid_response() from None
        return data

    def submit(self, job_id: str, request: dict[str, Any], *, intent: str) -> dict[str, Any]:
        self._bound()
        try:
            job_id = identifier(job_id)
            validate(request, self.site_id)
        except ContractError:
            raise NvkeError("INVALID_INPUT", "Review the pinned operator command and artifact inputs") from None
        return self._receipt(
            self._request("PUT", "/v1/jobs/" + job_id, body=request, intent=intent), job_id, request["command"], intent
        )

    def observe(self, job_id: str, *, command: str, intent: str) -> dict[str, Any]:
        self._bound()
        job_id = identifier(job_id)
        return self._receipt(self._request("GET", "/v1/jobs/" + job_id, intent=intent), job_id, command, intent)

    def cancel(self, job_id: str, *, command: str, intent: str) -> dict[str, Any]:
        self._bound()
        job_id = identifier(job_id)
        return self._receipt(
            self._request("POST", "/v1/jobs/" + job_id + "/cancel", body={}, intent=intent), job_id, command, intent
        )

    def stop_or_fence(self, job_id: str, *, command: str, intent: str, effectful: bool) -> dict[str, Any]:
        self._bound()
        try:
            job_id = identifier(job_id)
            if (
                command not in CATALOG
                or type(effectful) is not bool
                or not isinstance(intent, str)
                or not _SHA.fullmatch(intent)
            ):
                raise ValueError
        except (ContractError, TypeError, ValueError):
            raise NvkeError(
                "INVALID_INPUT", "Review the original operator job identity and effect classification"
            ) from None
        control = self.identity().get("jobControl")
        if not isinstance(control, dict) or (
            set(control) != {"format", "stopFence", "completeAdmissionHistory"}
            or type(control["format"]) is not int
            or control["format"] != 1
            or control["stopFence"] is not True
            or type(control["completeAdmissionHistory"]) is not bool
        ):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The reviewed operator agent does not confirm durable job control"
            )
        reviewed = {
            "format": 1,
            "revision": REVISION,
            "siteId": self.site_id,
            "bundleManifestSha256": self.bundle_digest,
            "command": command,
            "effectful": effectful,
        }
        result = self._receipt(
            self._request("POST", "/v1/jobs/" + job_id + "/stop", body=reviewed, intent=intent),
            job_id,
            command,
            intent,
        )
        if result["effectful"] != effectful or not result["cancellationRequested"]:
            raise _invalid_response()
        if result.get("errorCode") == "ORIGINAL_JOB_FENCED" and (
            result["state"] != "cancelled" or result["artifacts"] or "nativeExitCode" in result
        ):
            raise _invalid_response()
        return result

    def install_review(self, job_id: str, *, intent: str) -> dict[str, Any]:
        self._bound()
        try:
            job_id = identifier(job_id)
            if not isinstance(intent, str) or not _SHA.fullmatch(intent):
                raise ValueError
        except (ContractError, ValueError, TypeError):
            raise NvkeError("INVALID_INPUT", "Use the original management-install identity and intent") from None
        capability = self.identity().get("installReview")
        if (
            not isinstance(capability, dict)
            or set(capability) != {"format", "readOnly", "continuation"}
            or type(capability["format"]) is not int
            or capability["format"] != 1
            or capability["readOnly"] is not True
            or capability["continuation"] is not False
        ):
            raise NvkeError("OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm read-only install review")
        original = self.observe(job_id, command="management.install", intent=intent)
        if (
            original["state"] != "uncertain"
            or original["effectful"] is not True
            or (type(original.get("nativeExitCode")) is not int or original["nativeExitCode"] == 0)
        ):
            raise NvkeError("PRECONDITION", "Review requires the original retained unsuccessful install receipt")
        data = self._request("GET", "/v1/jobs/" + job_id + "/install-review", intent=intent)
        try:
            result = project_install_review(
                data,
                site=self.site_id,
                instance=str(self.instance_id),
                bundle=str(self.bundle_digest),
                job=job_id,
                intent=intent,
                exit_code=original["nativeExitCode"],
            )
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None
        self.identity()
        return result

    def artifact(
        self, job_id: str, *, command: str, intent: str, name: str, offset: str = "0", limit: int = MAX_CHUNK_BYTES
    ) -> dict[str, Any]:
        receipt = self.observe(job_id, command=command, intent=intent)
        selected = next((item for item in receipt["artifacts"] if item["name"] == name), None)
        if (
            selected is None
            or not isinstance(offset, str)
            or not _DECIMAL.fullmatch(offset)
            or type(limit) is not int
            or not 1 <= limit <= MAX_CHUNK_BYTES
        ):
            raise NvkeError("INVALID_INPUT", "Choose a retained artifact and a bounded byte range")
        data = self._request(
            "GET",
            "/v1/jobs/" + identifier(job_id) + "/artifact",
            params={"name": name, "offset": offset, "limit": limit},
            intent=intent,
        )
        try:
            if (
                set(data) != {"id", "name", "offset", "nextOffset", "size", "sha256", "dataBase64"}
                or data["id"] != job_id
                or data["name"] != name
                or data["offset"] != offset
                or data["size"] != selected["size"]
                or data["sha256"] != selected["sha256"]
                or not isinstance(data["nextOffset"], str)
                or not _DECIMAL.fullmatch(data["nextOffset"])
                or not isinstance(data["dataBase64"], str)
            ):
                raise ValueError
            chunk = base64.b64decode(data["dataBase64"], validate=True)
            if (
                len(chunk) > limit
                or int(data["nextOffset"]) != int(offset) + len(chunk)
                or int(data["nextOffset"]) > int(data["size"])
                or (int(offset) < int(data["size"]) and not chunk)
            ):
                raise ValueError
            if (
                offset == "0"
                and data["nextOffset"] == data["size"]
                and hashlib.sha256(chunk).hexdigest() != data["sha256"]
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise _invalid_response() from None
        return data
