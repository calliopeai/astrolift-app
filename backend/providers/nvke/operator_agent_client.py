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

from nvke import _operator_install_plan as install_plan_protocol
from nvke import _operator_install_plan_charts as chart_protocol
from nvke import _operator_install_plan_context as context_protocol
from nvke import _operator_install_plan_image as image_protocol
from nvke import _operator_install_plan_image_binding as binding_protocol
from nvke import _operator_install_plan_immutable as immutable_protocol
from nvke import _operator_install_plan_node_platforms as node_platform_protocol
from nvke import _operator_install_plan_pull_authority as pull_authority_protocol
from nvke import _operator_install_plan_pull_runtime as pull_runtime_protocol
from nvke import _operator_install_plan_pull_secret_analysis as pull_secret_analysis_protocol
from nvke import _operator_install_plan_tree as tree_protocol
from nvke import _operator_install_plan_verification as verification_protocol
from nvke import _operator_install_plan_workload as workload_protocol
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
    validate_admission,
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
                    "INSTALL_PLAN_CHARTS_INCOMPLETE": "OPERATOR_CHARTS_INCOMPLETE",
                    "ARTIFACT_INTENT_CHANGED": "OPERATOR_ARTIFACT_INTENT_CONFLICT",
                    "INSTALL_PLAN_SOURCE_CHANGED": "SOURCE_CHANGED",
                    "INSTALL_PLAN_CONTENT_CHANGED": "SOURCE_CHANGED",
                    "INSTALL_PLAN_TREE_INCOMPLETE": "OPERATOR_TREE_INCOMPLETE",
                    "EXECUTION_TREE_INCOMPLETE": "OPERATOR_TREE_INCOMPLETE",
                    "EXECUTION_TREE_INTENT_CHANGED": "OPERATOR_TREE_INTENT_CONFLICT",
                    "EXECUTION_TREE_CONTENT_CHANGED": "SOURCE_CHANGED",
                    "EXECUTION_TREE_CHART_SOURCE_CHANGED": "SOURCE_CHANGED",
                    "EXECUTION_TREE_SOURCE_OR_INPUT_CHANGED": "SOURCE_CHANGED",
                    "EXECUTION_TREE_BUSY": "OPERATOR_BUSY",
                    "INSTALL_PLAN_CONTEXT_INCOMPLETE": "OPERATOR_CONTEXT_INCOMPLETE",
                    "INSTALL_PLAN_VERIFICATION_INCOMPLETE": "OPERATOR_VERIFICATION_INCOMPLETE",
                    "INSTALL_PLAN_IMAGE_INCOMPLETE": "OPERATOR_IMAGE_INCOMPLETE",
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
            validate_admission(request, self.site_id)
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

    def _install_plan_source(self, job_id: str, intent: str) -> dict[str, Any]:
        self._bound()
        try:
            identifier(job_id)
            if not isinstance(intent, str) or not _SHA.fullmatch(intent):
                raise ValueError
        except (ContractError, ValueError, TypeError):
            raise NvkeError("INVALID_INPUT", "Use the original management-install identity and intent") from None
        capability = self.identity().get("installPlan")
        if (
            not isinstance(capability, dict)
            or set(capability) != {"format", "prepare", "readOnly", "continuation", "maximumPageSize"}
            or type(capability["format"]) is not int
            or capability["format"] != 1
            or capability["prepare"] is not True
            or capability["readOnly"] is not True
            or capability["continuation"] is not False
            or type(capability["maximumPageSize"]) is not int
            or capability["maximumPageSize"] != 100
        ):
            raise NvkeError("OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm read-only plan preparation")
        original = self.observe(job_id, command="management.install", intent=intent)
        if (
            original["state"] != "uncertain"
            or original["effectful"] is not True
            or type(original.get("nativeExitCode")) is not int
            or original["nativeExitCode"] == 0
        ):
            raise NvkeError("PRECONDITION", "Preparation requires the original unsuccessful install receipt")
        return original

    def _install_plan_summary(
        self, data: dict[str, Any], job_id: str, plan_id: str, original: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return install_plan_protocol.summary(
                data,
                site=self.site_id,
                instance=str(self.instance_id),
                bundle=str(self.bundle_digest),
                job=job_id,
                plan_id=plan_id,
                exit_code=original["nativeExitCode"],
            )
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan(self, job_id: str, *, intent: str, body: dict[str, Any]) -> dict[str, Any]:
        self._bound()
        try:
            body = install_plan_protocol.preparation_request(body, site=self.site_id, job_id=job_id)
            plan_id = body["planId"]
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use a bounded reviewed management-install plan request") from None
        original = self._install_plan_source(job_id, intent)
        data = self._request("POST", "/v1/jobs/" + job_id + "/install-plan", body=body, intent=intent)
        result = self._install_plan_summary(data, job_id, plan_id, original)
        if self._install_plan_source(job_id, intent) != original:
            raise _invalid_response()
        return result

    def install_plan(self, job_id: str, *, intent: str, plan_id: str) -> dict[str, Any]:
        try:
            identifier(plan_id)
        except (ContractError, ValueError, TypeError):
            raise NvkeError("INVALID_INPUT", "Use the retained plan identity") from None
        original = self._install_plan_source(job_id, intent)
        data = self._request("GET", "/v1/jobs/" + job_id + "/install-plan", params={"planId": plan_id}, intent=intent)
        result = self._install_plan_summary(data, job_id, plan_id, original)
        if self._install_plan_source(job_id, intent) != original:
            raise _invalid_response()
        return result

    def install_plan_page(
        self,
        job_id: str,
        *,
        intent: str,
        plan_id: str,
        plan_digest: str,
        view: str,
        after: str | None = None,
        limit: int = 25,
        search: str = "",
    ) -> dict[str, Any]:
        if (
            view not in {"items", "workloads"}
            or type(limit) is not int
            or not 1 <= limit <= 100
            or not isinstance(search, str)
            or len(search) > 200
            or (after is not None and (not isinstance(after, str) or not 1 <= len(after) <= 8192))
            or not isinstance(plan_digest, str)
            or not _SHA.fullmatch(plan_digest)
        ):
            raise NvkeError("INVALID_INPUT", "Use the retained plan digest and a bounded page request")
        metadata = self.install_plan(job_id, intent=intent, plan_id=plan_id)
        if metadata["plan"]["planDigest"] != plan_digest:
            raise NvkeError("SOURCE_CHANGED", "The retained plan no longer matches the reviewed digest")
        params = {"planId": plan_id, "view": view, "limit": limit, "search": search}
        if after is not None:
            params["after"] = after
        data = self._request("GET", "/v1/jobs/" + job_id + "/install-plan", params=params, intent=intent)
        try:
            result = install_plan_protocol.page(
                data, metadata=metadata, view=view, limit=limit, search=search, initial=after is None
            )
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None
        if self.install_plan(job_id, intent=intent, plan_id=plan_id) != metadata:
            raise _invalid_response()
        return result

    def _install_plan_charts(
        self, job_id: str, *, intent: str, body: dict[str, Any], read_only: bool
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = chart_protocol.request(body, job_id=job_id, read_only=read_only)
            if not isinstance(intent, str) or not _SHA.fullmatch(intent):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use a retained plan and bounded chart-artifact request") from None
        try:
            chart_protocol.capability(self.identity().get("installPlanCharts"))
        except (KeyError, ValueError, TypeError):
            raise NvkeError("OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm chart preparation") from None
        metadata = self.install_plan(job_id, intent=intent, plan_id=body["planId"])
        if metadata["plan"]["planDigest"] != body["planDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The retained plan no longer matches the reviewed digest")
        path = "/v1/jobs/" + job_id + "/install-plan-charts"
        data = (
            self._request(
                "GET", path, params={key: value for key, value in body.items() if key != "format"}, intent=intent
            )
            if read_only
            else self._request("POST", path, body=body, intent=intent)
        )
        try:
            result = chart_protocol.summary(data, body=body, item_count=metadata["itemCount"])
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None
        if self.install_plan(job_id, intent=intent, plan_id=body["planId"]) != metadata:
            raise _invalid_response()
        try:
            chart_protocol.capability(self.identity().get("installPlanCharts"))
        except (KeyError, ValueError, TypeError):
            raise _invalid_response() from None
        return result

    def prepare_install_plan_charts(self, job_id: str, *, intent: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._install_plan_charts(job_id, intent=intent, body=body, read_only=False)

    def install_plan_charts(
        self, job_id: str, *, intent: str, plan_id: str, plan_digest: str, artifact_id: str
    ) -> dict[str, Any]:
        return self._install_plan_charts(
            job_id,
            intent=intent,
            body={"format": 1, "planId": plan_id, "planDigest": plan_digest, "artifactId": artifact_id},
            read_only=True,
        )

    def _install_plan_tree_source(
        self, job_id: str, intent: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = self.install_plan(job_id, intent=intent, plan_id=body["planId"])
        if metadata["plan"]["planDigest"] != body["planDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The retained plan no longer matches the reviewed digest")
        publication = self.install_plan_charts(
            job_id,
            intent=intent,
            plan_id=body["planId"],
            plan_digest=body["planDigest"],
            artifact_id=body["chartArtifactId"],
        )
        if publication["artifactDigest"] != body["chartArtifactDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The chart publication no longer matches the reviewed digest")
        if self.install_plan(job_id, intent=intent, plan_id=body["planId"]) != metadata:
            raise _invalid_response()
        return metadata, publication

    def _install_plan_tree(self, job_id: str, *, intent: str, body: dict[str, Any], read_only: bool) -> dict[str, Any]:
        self._bound()
        try:
            body = tree_protocol.request(body, job_id=job_id, read_only=read_only)
            if not isinstance(intent, str) or not _SHA.fullmatch(intent):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError(
                "INVALID_INPUT", "Use an exact plan/chart publication and bounded execution-tree request"
            ) from None
        try:
            tree_protocol.capability(self.identity().get("installPlanTree"))
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm execution-tree preparation"
            ) from None
        source = self._install_plan_tree_source(job_id, intent, body)
        path = "/v1/jobs/" + job_id + "/install-plan-tree"
        data = (
            self._request(
                "GET", path, params={key: value for key, value in body.items() if key != "format"}, intent=intent
            )
            if read_only
            else self._request("POST", path, body=body, intent=intent)
        )
        try:
            result = tree_protocol.summary(
                data, body=body, chart_bytes=source[1]["bytes"], chart_count=source[1]["chartCount"]
            )
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None
        if self._install_plan_tree_source(job_id, intent, body) != source:
            raise _invalid_response()
        try:
            tree_protocol.capability(self.identity().get("installPlanTree"))
        except (KeyError, ValueError, TypeError):
            raise _invalid_response() from None
        return result

    def prepare_install_plan_tree(self, job_id: str, *, intent: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._install_plan_tree(job_id, intent=intent, body=body, read_only=False)

    def install_plan_tree(
        self,
        job_id: str,
        *,
        intent: str,
        plan_id: str,
        plan_digest: str,
        chart_artifact_id: str,
        chart_artifact_digest: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        return self._install_plan_tree(
            job_id,
            intent=intent,
            body={
                "format": 1,
                "artifactId": artifact_id,
                "planId": plan_id,
                "planDigest": plan_digest,
                "chartArtifactId": chart_artifact_id,
                "chartArtifactDigest": chart_artifact_digest,
            },
            read_only=True,
        )

    def _install_plan_artifact_source(
        self,
        job_id: str,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        capability_name: str,
    ) -> dict[str, Any]:
        original = self._install_plan_source(job_id, intent)
        identity = self.identity()
        for name in ("installPlanCharts", "installPlanTree", capability_name):
            image_protocol.capability(identity.get(name))
        parents = {**body, "chartArtifactId": chart_artifact_id, "chartArtifactDigest": chart_artifact_digest}
        metadata, publication = self._install_plan_tree_source(job_id, intent, parents)
        tree = self.install_plan_tree(
            job_id,
            intent=intent,
            plan_id=body["planId"],
            plan_digest=body["planDigest"],
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            artifact_id=body["treeId"],
        )
        if tree["treeDigest"] != body["treeDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The execution tree no longer matches the reviewed digest")
        return {"original": original, "identity": identity, "metadata": metadata, "charts": publication, "tree": tree}

    def _install_plan_image_source(
        self, job_id: str, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        source = self._install_plan_artifact_source(
            job_id, intent, body, chart_artifact_id, chart_artifact_digest, image_protocol.capability_name(body)
        )
        if "workloadId" in body:
            workload = self.install_plan_workload(
                job_id,
                intent=intent,
                chart_artifact_id=chart_artifact_id,
                chart_artifact_digest=chart_artifact_digest,
                body={
                    **{key: body[key] for key in ("format", "planId", "planDigest", "treeId", "treeDigest")},
                    "artifactId": body["workloadId"],
                },
            )
            if workload["workloadDigest"] != body["workloadDigest"]:
                raise NvkeError("SOURCE_CHANGED", "The workload inventory no longer matches the reviewed digest")
            source["workload"] = workload
        return source

    def _install_plan_workload(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = workload_protocol.request(
                body, job_id=job_id, chart_artifact_id=chart_artifact_id, read_only=read_only
            )
            if (
                not isinstance(intent, str)
                or not _SHA.fullmatch(intent)
                or not isinstance(chart_artifact_digest, str)
                or not _SHA.fullmatch(chart_artifact_digest)
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use a reviewed tree and bounded workload-inventory request") from None
        try:
            workload_protocol.capability(self.identity().get("installPlanWorkloadInventory"))
        except (KeyError, ValueError, TypeError):
            raise NvkeError("OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm workload inventory") from None
        try:
            source = self._install_plan_artifact_source(
                job_id, intent, body, chart_artifact_id, chart_artifact_digest, "installPlanWorkloadInventory"
            )
            path = "/v1/jobs/" + job_id + "/install-plan-workload-inventory"
            data = (
                self._request(
                    "GET", path, params={key: value for key, value in body.items() if key != "format"}, intent=intent
                )
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = workload_protocol.summary(data, body=body, item_count=source["metadata"]["itemCount"])
            if (
                self._install_plan_artifact_source(
                    job_id, intent, body, chart_artifact_id, chart_artifact_digest, "installPlanWorkloadInventory"
                )
                != source
            ):
                raise ValueError
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_workload(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_workload(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def install_plan_workload(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_workload(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def _install_plan_image_binding_source(
        self, job_id: str, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        source = self._install_plan_artifact_source(
            job_id, intent, body, chart_artifact_id, chart_artifact_digest, "installPlanImageBinding"
        )
        workload_body = workload_protocol.request(
            {
                **{key: body[key] for key in ("format", "planId", "planDigest", "treeId", "treeDigest")},
                "artifactId": body["workloadId"],
            },
            job_id=job_id,
            chart_artifact_id=chart_artifact_id,
            read_only=True,
        )
        workload_protocol.capability(self.identity().get("installPlanWorkloadInventory"))
        workload = workload_protocol.summary(
            self._request(
                "GET",
                "/v1/jobs/" + job_id + "/install-plan-workload-inventory",
                params={key: value for key, value in workload_body.items() if key != "format"},
                intent=intent,
            ),
            body=workload_body,
            item_count=source["metadata"]["itemCount"],
        )
        workload_protocol.capability(self.identity().get("installPlanWorkloadInventory"))
        if workload["workloadDigest"] != body["workloadDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The workload inventory no longer matches the reviewed digest")
        return {**source, "workload": workload}

    def _install_plan_image_binding(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = binding_protocol.request(
                body, job_id=job_id, chart_artifact_id=chart_artifact_id, read_only=read_only
            )
            if (
                not isinstance(intent, str)
                or not _SHA.fullmatch(intent)
                or not isinstance(chart_artifact_digest, str)
                or not _SHA.fullmatch(chart_artifact_digest)
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use a reviewed tree and bounded image-binding request") from None
        try:
            binding_protocol.capability(self.identity().get("installPlanImageBinding"))
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm signed image bindings"
            ) from None
        try:
            source = self._install_plan_image_binding_source(
                job_id, intent, body, chart_artifact_id, chart_artifact_digest
            )
            path = "/v1/jobs/" + job_id + "/install-plan-image-binding"
            data = (
                self._request(
                    "GET", path, params={key: value for key, value in body.items() if key != "format"}, intent=intent
                )
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = binding_protocol.summary(data, body=body, workload=source["workload"])
            if (
                self._install_plan_image_binding_source(job_id, intent, body, chart_artifact_id, chart_artifact_digest)
                != source
            ):
                raise ValueError
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_image_binding(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_image_binding(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def install_plan_image_binding(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_image_binding(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def _install_plan_immutable_source(
        self,
        job_id: str,
        intent: str,
        body: dict[str, Any],
        *,
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
        payload_bindings: dict[str, Any] | None,
    ) -> dict[str, Any]:
        binding_body = {
            **{k: body[k] for k in ("format", "planId", "planDigest", "treeId", "treeDigest")},
            "artifactId": body["bindingId"],
            "workloadId": workload_id,
            "workloadDigest": workload_digest,
        }
        binding_body = binding_protocol.request(
            binding_body, job_id=job_id, chart_artifact_id=chart_artifact_id, read_only=True
        )
        source = self._install_plan_image_binding_source(
            job_id, intent, binding_body, chart_artifact_id, chart_artifact_digest
        )
        data = self._request(
            "GET",
            "/v1/jobs/" + job_id + "/install-plan-image-binding",
            params={k: v for k, v in binding_body.items() if k != "format"},
            intent=intent,
        )
        binding = binding_protocol.summary(data, body=binding_body, workload=source["workload"])
        if binding["bindingDigest"] != body["bindingDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The image binding no longer matches the reviewed digest")
        source["binding"] = binding
        if payload_bindings is not None:
            if len(payload_bindings) != source["metadata"]["itemCount"]:
                raise NvkeError("SOURCE_CHANGED", "Select an immutable payload for every original phase")
            selected = {}
            for name, row in payload_bindings.items():
                query = {
                    **{k: body[k] for k in immutable_protocol.BASE if k != "artifactId"},
                    "artifactId": row["artifactId"],
                    "item": name,
                }
                data = self._request(
                    "GET",
                    "/v1/jobs/" + job_id + "/install-plan-image-payload",
                    params={k: v for k, v in query.items() if k != "format"},
                    intent=intent,
                )
                value = immutable_protocol.summary(data, body=query, source=source, tree=False)
                if value["artifactDigest"] != row["artifactDigest"]:
                    raise NvkeError("SOURCE_CHANGED", "A phase payload no longer matches its reviewed digest")
                selected[name] = value
            source["payloads"] = selected
        return source

    def _install_plan_pull_authority(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = pull_authority_protocol.request(
                body,
                job_id=job_id,
                chart_artifact_id=chart_artifact_id,
                workload_id=workload_id,
                read_only=read_only,
            )
            if any(
                not isinstance(v, str) or not _SHA.fullmatch(v)
                for v in (
                    intent,
                    chart_artifact_digest,
                    workload_digest,
                )
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use the reviewed image binding, explicit Pod UIDs and bounds") from None
        capability = "installPlanPullAuthority"
        try:
            pull_authority_protocol.capability(self.identity().get(capability), read_only=read_only)
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm pull authority observation controls"
            ) from None
        try:

            def ancestry() -> dict[str, Any]:
                return self._install_plan_immutable_source(
                    job_id,
                    intent,
                    body,
                    chart_artifact_id=chart_artifact_id,
                    chart_artifact_digest=chart_artifact_digest,
                    workload_id=workload_id,
                    workload_digest=workload_digest,
                    payload_bindings=None,
                )

            source = ancestry()
            path = "/v1/jobs/" + job_id + "/install-plan-pull-authority"
            data = (
                self._request("GET", path, params={k: v for k, v in body.items() if k != "format"}, intent=intent)
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = pull_authority_protocol.summary(data, body=body, source=source)
            if ancestry() != source:
                raise _invalid_response()
            pull_authority_protocol.capability(self.identity().get(capability), read_only=read_only)
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_pull_authority(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_pull_authority(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def install_plan_pull_authority(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_pull_authority(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def _install_plan_pull_secret_analysis(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = pull_secret_analysis_protocol.request(
                body,
                job_id=job_id,
                chart_artifact_id=chart_artifact_id,
                workload_id=workload_id,
                read_only=read_only,
            )
            if any(
                not isinstance(v, str) or not _SHA.fullmatch(v)
                for v in (
                    intent,
                    chart_artifact_digest,
                    workload_digest,
                )
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use the reviewed image binding, explicit image uses and bounds") from None
        capability = "installPlanPullSecretAnalysis"
        try:
            pull_secret_analysis_protocol.capability(self.identity().get(capability), read_only=read_only)
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm private pull Secret analysis controls"
            ) from None
        try:

            def ancestry() -> dict[str, Any]:
                source = self._install_plan_immutable_source(
                    job_id,
                    intent,
                    body,
                    chart_artifact_id=chart_artifact_id,
                    chart_artifact_digest=chart_artifact_digest,
                    workload_id=workload_id,
                    workload_digest=workload_digest,
                    payload_bindings=None,
                )
                parent_body = {key: value for key, value in body.items() if key in immutable_protocol.BASE}
                parent_body["artifactId"] = body["pullAuthorityId"]
                immutable_protocol.capability(self.identity().get("installPlanPullAuthority"), read_only=True)
                captured = pull_authority_protocol.summary(
                    self._request(
                        "GET",
                        "/v1/jobs/" + job_id + "/install-plan-pull-authority",
                        params={key: value for key, value in parent_body.items() if key != "format"},
                        intent=intent,
                    ),
                    body=parent_body,
                    source=source,
                )
                immutable_protocol.capability(self.identity().get("installPlanPullAuthority"), read_only=True)
                if captured["authorityDigest"] != body["pullAuthorityDigest"]:
                    raise NvkeError("SOURCE_CHANGED", "The pull observation no longer matches its reviewed digest")
                return {**source, "pullAuthority": captured}

            source = ancestry()
            path = "/v1/jobs/" + job_id + "/install-plan-pull-secret-analysis"
            data = (
                self._request("GET", path, params={k: v for k, v in body.items() if k != "format"}, intent=intent)
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = pull_secret_analysis_protocol.summary(data, body=body, source=source)
            if ancestry() != source:
                raise _invalid_response()
            pull_secret_analysis_protocol.capability(self.identity().get(capability), read_only=read_only)
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_pull_secret_analysis(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_pull_secret_analysis(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def install_plan_pull_secret_analysis(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_pull_secret_analysis(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def install_plan_pull_runtime_correspondence(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = pull_runtime_protocol.request(
                body, job_id=job_id, chart_artifact_id=chart_artifact_id, workload_id=workload_id
            )
            if any(
                not isinstance(value, str) or not _SHA.fullmatch(value)
                for value in (intent, chart_artifact_digest, workload_digest)
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use exact reviewed analysis and node/platform digests") from None
        capability = "installPlanPullRuntimeCorrespondence"
        try:
            pull_runtime_protocol.capability(self.identity().get(capability))
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm read-only pull runtime correspondence"
            ) from None
        try:

            def ancestry() -> dict[str, Any]:
                source = self._install_plan_immutable_source(
                    job_id,
                    intent,
                    body,
                    chart_artifact_id=chart_artifact_id,
                    chart_artifact_digest=chart_artifact_digest,
                    workload_id=workload_id,
                    workload_digest=workload_digest,
                    payload_bindings=None,
                )
                identity = self.identity()
                for name in ("installPlanPullAuthority", "installPlanPullSecretAnalysis", "installPlanNodePlatforms"):
                    immutable_protocol.capability(identity.get(name), read_only=True)
                prefix = "/v1/jobs/" + job_id

                def read(suffix: str, query: dict[str, Any]) -> dict[str, Any]:
                    return self._request(
                        "GET",
                        prefix + suffix,
                        params={key: value for key, value in query.items() if key != "format"},
                        intent=intent,
                    )

                query = {key: body[key] for key in immutable_protocol.BASE}
                query["artifactId"] = body["pullAuthorityId"]
                pull = pull_authority_protocol.summary(
                    read("/install-plan-pull-authority", query), body=query, source=source
                )
                if pull["authorityDigest"] != body["pullAuthorityDigest"]:
                    raise NvkeError("SOURCE_CHANGED", "The original pull observation changed")
                source["pullAuthority"] = pull
                query = {key: body[key] for key in pull_secret_analysis_protocol.BASE}
                analysis = pull_secret_analysis_protocol.summary(
                    read("/install-plan-pull-secret-analysis", query), body=query, source=source
                )
                if analysis["authorityDigest"] != body["analysisDigest"]:
                    raise NvkeError("SOURCE_CHANGED", "The original Secret analysis changed")
                source["analysis"] = analysis
                query = {key: body[key] for key in immutable_protocol.BASE}
                query["artifactId"] = body["nodePlatformId"]
                platforms = node_platform_protocol.summary(
                    read("/install-plan-node-platforms", query), body=query, source=source
                )
                if platforms["platformDigest"] != body["nodePlatformDigest"]:
                    raise NvkeError("SOURCE_CHANGED", "The original node/platform observation changed")
                source["nodePlatforms"] = platforms
                return source

            source = ancestry()
            data = self._request(
                "GET",
                "/v1/jobs/" + job_id + "/install-plan-pull-runtime-correspondence",
                params={key: value for key, value in body.items() if key != "format"},
                intent=intent,
            )
            result = pull_runtime_protocol.summary(data, body=body, source=source)
            if ancestry() != source:
                raise _invalid_response()
            pull_runtime_protocol.capability(self.identity().get(capability))
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def _install_plan_node_platforms(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = node_platform_protocol.request(
                body,
                job_id=job_id,
                chart_artifact_id=chart_artifact_id,
                workload_id=workload_id,
                read_only=read_only,
            )
            if any(
                not isinstance(v, str) or not _SHA.fullmatch(v)
                for v in (
                    intent,
                    chart_artifact_digest,
                    workload_digest,
                )
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use the reviewed image binding, explicit nodes and bounds") from None
        capability = "installPlanNodePlatforms"
        try:
            node_platform_protocol.capability(self.identity().get(capability), read_only=read_only)
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm node platform controls"
            ) from None
        try:

            def ancestry() -> dict[str, Any]:
                return self._install_plan_immutable_source(
                    job_id,
                    intent,
                    body,
                    chart_artifact_id=chart_artifact_id,
                    chart_artifact_digest=chart_artifact_digest,
                    workload_id=workload_id,
                    workload_digest=workload_digest,
                    payload_bindings=None,
                )

            source = ancestry()
            path = "/v1/jobs/" + job_id + "/install-plan-node-platforms"
            data = (
                self._request("GET", path, params={k: v for k, v in body.items() if k != "format"}, intent=intent)
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = node_platform_protocol.summary(data, body=body, source=source)
            if ancestry() != source:
                raise _invalid_response()
            node_platform_protocol.capability(self.identity().get(capability), read_only=read_only)
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_node_platforms(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_node_platforms(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def install_plan_node_platforms(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_node_platforms(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def _install_plan_immutable(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        tree: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
        payload_bindings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = immutable_protocol.request(
                body,
                job_id=job_id,
                chart_artifact_id=chart_artifact_id,
                workload_id=workload_id,
                read_only=read_only,
                tree=tree,
            )
            if any(
                not isinstance(v, str) or not _SHA.fullmatch(v)
                for v in (intent, chart_artifact_digest, workload_digest)
            ):
                raise ValueError
            selected = None
            if tree:
                selected = immutable_protocol.selections(
                    payload_bindings if read_only else body["payloadBindings"],
                    reserved={
                        job_id,
                        chart_artifact_id,
                        workload_id,
                        body["artifactId"],
                        body["planId"],
                        body["treeId"],
                        body["bindingId"],
                    },
                )
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError(
                "INVALID_INPUT", "Use the reviewed binding, original phase payloads and explicit bounds"
            ) from None
        capability_name = "installPlanImmutableExecutionTree" if tree else "installPlanImagePayload"
        try:
            immutable_protocol.capability(self.identity().get(capability_name), read_only=read_only)
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm immutable artifact controls"
            ) from None
        try:

            def ancestry() -> dict[str, Any]:
                return self._install_plan_immutable_source(
                    job_id,
                    intent,
                    body,
                    chart_artifact_id=chart_artifact_id,
                    chart_artifact_digest=chart_artifact_digest,
                    workload_id=workload_id,
                    workload_digest=workload_digest,
                    payload_bindings=selected,
                )

            source = ancestry()
            path = (
                "/v1/jobs/"
                + job_id
                + ("/install-plan-immutable-execution-tree" if tree else "/install-plan-image-payload")
            )
            data = (
                self._request("GET", path, params={k: v for k, v in body.items() if k != "format"}, intent=intent)
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = immutable_protocol.summary(data, body=body, source=source, tree=tree)
            if ancestry() != source:
                raise _invalid_response()
            immutable_protocol.capability(self.identity().get(capability_name), read_only=read_only)
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_image_payload(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_immutable(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            tree=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def install_plan_image_payload(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_immutable(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            tree=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def prepare_install_plan_immutable_execution_tree(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
    ) -> dict[str, Any]:
        return self._install_plan_immutable(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            tree=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
        )

    def install_plan_immutable_execution_tree(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        chart_artifact_id: str,
        chart_artifact_digest: str,
        workload_id: str,
        workload_digest: str,
        payload_bindings: dict[str, Any],
    ) -> dict[str, Any]:
        return self._install_plan_immutable(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            tree=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
            workload_id=workload_id,
            workload_digest=workload_digest,
            payload_bindings=payload_bindings,
        )

    def _install_plan_image(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = image_protocol.request(body, job_id=job_id, chart_artifact_id=chart_artifact_id, read_only=read_only)
            if (
                not isinstance(intent, str)
                or not _SHA.fullmatch(intent)
                or not isinstance(chart_artifact_digest, str)
                or not _SHA.fullmatch(chart_artifact_digest)
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError(
                "INVALID_INPUT", "Use a reviewed tree and a closed bounded registry or local-image request"
            ) from None
        try:
            image_protocol.capability(self.identity().get(image_protocol.capability_name(body)))
        except (KeyError, ValueError, TypeError):
            raise NvkeError("OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm image acquisition") from None
        try:
            source = self._install_plan_image_source(job_id, intent, body, chart_artifact_id, chart_artifact_digest)
            path = "/v1/jobs/" + job_id + "/install-plan-image"
            data = (
                self._request(
                    "GET", path, params={key: value for key, value in body.items() if key != "format"}, intent=intent
                )
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = image_protocol.summary(data, body=body)
            if (
                self._install_plan_image_source(job_id, intent, body, chart_artifact_id, chart_artifact_digest)
                != source
            ):
                raise ValueError
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_image(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_image(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def install_plan_image(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_image(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def _install_plan_context_capabilities(self) -> None:
        identity = self.identity()
        for name in ("installPlanCharts", "installPlanTree", "installPlanContext"):
            context_protocol.capability(identity.get(name))

    def _install_plan_context_snapshot(
        self, job_id: str, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        original = self._install_plan_source(job_id, intent)
        path = "/v1/jobs/" + job_id
        metadata = self._install_plan_summary(
            self._request("GET", path + "/install-plan", params={"planId": body["planId"]}, intent=intent),
            job_id,
            body["planId"],
            original,
        )
        if metadata["plan"]["planDigest"] != body["planDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The retained plan no longer matches the reviewed digest")
        chart_body = {
            "format": 1,
            "artifactId": chart_artifact_id,
            "planId": body["planId"],
            "planDigest": body["planDigest"],
        }
        chart = chart_protocol.summary(
            self._request(
                "GET",
                path + "/install-plan-charts",
                params={k: v for k, v in chart_body.items() if k != "format"},
                intent=intent,
            ),
            body=chart_body,
            item_count=metadata["itemCount"],
        )
        if chart["artifactDigest"] != chart_artifact_digest:
            raise NvkeError("SOURCE_CHANGED", "The chart publication no longer matches the reviewed digest")
        tree_body = {
            **chart_body,
            "artifactId": body["treeId"],
            "chartArtifactId": chart_artifact_id,
            "chartArtifactDigest": chart_artifact_digest,
        }
        tree = tree_protocol.summary(
            self._request(
                "GET",
                path + "/install-plan-tree",
                params={k: v for k, v in tree_body.items() if k != "format"},
                intent=intent,
            ),
            body=tree_body,
            chart_bytes=chart["bytes"],
            chart_count=chart["chartCount"],
        )
        if tree["treeDigest"] != body["treeDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The execution tree no longer matches the reviewed digest")
        selected, pages, names, cursors = [], [], set(), set()
        after, total, previous = None, None, -1
        for _ in range(100):
            params = {"planId": body["planId"], "view": "items", "limit": 100, "search": body["item"]}
            if after is not None:
                params["after"] = after
            page = install_plan_protocol.page(
                self._request("GET", path + "/install-plan", params=params, intent=intent),
                metadata=metadata,
                view="items",
                limit=100,
                search=body["item"],
                initial=after is None,
            )
            pages.append(page)
            if total is not None and page["totalCount"] != total:
                raise ValueError
            total = page["totalCount"]
            for row in page["items"]:
                if row["name"] in names or row["ordinal"] <= previous:
                    raise ValueError
                names.add(row["name"])
                previous = row["ordinal"]
                if row["name"] == body["item"]:
                    selected.append(row)
            after = page["nextCursor"]
            if after is None:
                break
            if after in cursors:
                raise ValueError
            cursors.add(after)
        else:
            raise ValueError
        if len(names) != total:
            raise ValueError
        if len(selected) != 1 or selected[0]["type"] != "helm":
            raise NvkeError("SOURCE_CHANGED", "The reviewed Helm item is absent from the retained plan")
        if self._install_plan_source(job_id, intent) != original:
            raise ValueError
        self._install_plan_context_capabilities()
        return {
            "original": original,
            "plan": metadata,
            "chart": chart,
            "tree": tree,
            "pages": pages,
            "item": selected[0],
        }

    def _install_plan_context_source(
        self, job_id: str, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        try:
            first = self._install_plan_context_snapshot(job_id, intent, body, chart_artifact_id, chart_artifact_digest)
            if (
                self._install_plan_context_snapshot(job_id, intent, body, chart_artifact_id, chart_artifact_digest)
                != first
            ):
                raise ValueError
            return first
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def _install_plan_context(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = context_protocol.request(
                body, job_id=job_id, read_only=read_only, chart_artifact_id=chart_artifact_id
            )
            if (
                not isinstance(intent, str)
                or not _SHA.fullmatch(intent)
                or not isinstance(chart_artifact_digest, str)
                or not _SHA.fullmatch(chart_artifact_digest)
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError(
                "INVALID_INPUT", "Use a reviewed tree, Helm item and bounded render-context request"
            ) from None
        try:
            self._install_plan_context_capabilities()
        except (KeyError, ValueError, TypeError):
            raise NvkeError(
                "OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm render-context capture"
            ) from None
        source = self._install_plan_context_source(job_id, intent, body, chart_artifact_id, chart_artifact_digest)
        path = "/v1/jobs/" + job_id + "/install-plan-context"
        data = (
            self._request(
                "GET", path, params={key: value for key, value in body.items() if key != "format"}, intent=intent
            )
            if read_only
            else self._request("POST", path, body=body, intent=intent)
        )
        try:
            result = context_protocol.summary(data, body=body, item=source["item"])
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None
        if self._install_plan_context_source(job_id, intent, body, chart_artifact_id, chart_artifact_digest) != source:
            raise _invalid_response()
        try:
            self._install_plan_context_capabilities()
        except (KeyError, ValueError, TypeError):
            raise _invalid_response() from None
        return result

    def prepare_install_plan_context(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_context(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def install_plan_context(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_context(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def _install_plan_verification_source(
        self, job_id: str, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        original = self._install_plan_source(job_id, intent)
        identity = self.identity()
        for name in ("installPlanCharts", "installPlanTree", "installPlanContext", "installPlanRenderVerification"):
            verification_protocol.capability(identity.get(name))
        path = "/v1/jobs/" + job_id
        metadata = self._install_plan_summary(
            self._request("GET", path + "/install-plan", params={"planId": body["planId"]}, intent=intent),
            job_id,
            body["planId"],
            original,
        )
        if metadata["plan"]["planDigest"] != body["planDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The retained plan no longer matches the reviewed digest")
        chart_body = {
            "format": 1,
            "artifactId": chart_artifact_id,
            "planId": body["planId"],
            "planDigest": body["planDigest"],
        }
        chart = chart_protocol.summary(
            self._request(
                "GET",
                path + "/install-plan-charts",
                params={key: value for key, value in chart_body.items() if key != "format"},
                intent=intent,
            ),
            body=chart_body,
            item_count=metadata["itemCount"],
        )
        if chart["artifactDigest"] != chart_artifact_digest:
            raise NvkeError("SOURCE_CHANGED", "The chart publication no longer matches the reviewed digest")
        tree_body = {
            **chart_body,
            "artifactId": body["treeId"],
            "chartArtifactId": chart_artifact_id,
            "chartArtifactDigest": chart_artifact_digest,
        }
        tree = tree_protocol.summary(
            self._request(
                "GET",
                path + "/install-plan-tree",
                params={key: value for key, value in tree_body.items() if key != "format"},
                intent=intent,
            ),
            body=tree_body,
            chart_bytes=chart["bytes"],
            chart_count=chart["chartCount"],
        )
        if tree["treeDigest"] != body["treeDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The execution tree no longer matches the reviewed digest")
        selected, pages, names, cursors = [], [], set(), set()
        after = None
        total, last_ordinal = None, -1
        for _ in range(100):
            params = {"planId": body["planId"], "view": "items", "limit": 100, "search": body["item"]}
            if after is not None:
                params["after"] = after
            page = install_plan_protocol.page(
                self._request("GET", path + "/install-plan", params=params, intent=intent),
                metadata=metadata,
                view="items",
                limit=100,
                search=body["item"],
                initial=after is None,
            )
            pages.append(page)
            if total is not None and page["totalCount"] != total:
                raise ValueError
            total = page["totalCount"]
            for row in page["items"]:
                if row["name"] in names or row["ordinal"] <= last_ordinal:
                    raise ValueError
                last_ordinal = row["ordinal"]
                names.add(row["name"])
                if row["name"] == body["item"]:
                    selected.append(row)
            after = page["nextCursor"]
            if after is None:
                break
            if after in cursors:
                raise ValueError
            cursors.add(after)
        else:
            raise ValueError
        if len(names) != total:
            raise ValueError
        if len(selected) != 1 or selected[0]["type"] != "helm":
            raise NvkeError("SOURCE_CHANGED", "The reviewed Helm item is absent from the retained plan")
        context_body = {
            "format": 1,
            **{key: body[key] for key in ("planId", "planDigest", "treeId", "treeDigest", "item")},
            "artifactId": body["contextId"],
        }
        context = context_protocol.summary(
            self._request(
                "GET",
                path + "/install-plan-context",
                params={key: value for key, value in context_body.items() if key != "format"},
                intent=intent,
            ),
            body=context_body,
            item=selected[0],
        )
        if context["contextDigest"] != body["contextDigest"]:
            raise NvkeError("SOURCE_CHANGED", "The captured context no longer matches the reviewed digest")
        if self._install_plan_source(job_id, intent) != original:
            raise ValueError
        identity = self.identity()
        for name in ("installPlanCharts", "installPlanTree", "installPlanContext", "installPlanRenderVerification"):
            verification_protocol.capability(identity.get(name))
        return {
            "original": original,
            "plan": metadata,
            "chart": chart,
            "tree": tree,
            "pages": pages,
            "context": context,
        }

    def _install_plan_verification(
        self,
        job_id: str,
        *,
        intent: str,
        body: dict[str, Any],
        read_only: bool,
        chart_artifact_id: str,
        chart_artifact_digest: str,
    ) -> dict[str, Any]:
        self._bound()
        try:
            body = verification_protocol.request(
                body, job_id=job_id, chart_artifact_id=chart_artifact_id, read_only=read_only
            )
            if (
                not isinstance(intent, str)
                or not _SHA.fullmatch(intent)
                or not isinstance(chart_artifact_digest, str)
                or not _SHA.fullmatch(chart_artifact_digest)
            ):
                raise ValueError
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise NvkeError("INVALID_INPUT", "Use an exact captured context and bounded verification request") from None
        try:
            verification_protocol.capability(self.identity().get("installPlanRenderVerification"))
        except (KeyError, ValueError, TypeError):
            raise NvkeError("OPERATOR_CONTROL_UNSUPPORTED", "The agent does not confirm render verification") from None
        try:
            source = self._install_plan_verification_source(
                job_id, intent, body, chart_artifact_id, chart_artifact_digest
            )
            path = "/v1/jobs/" + job_id + "/install-plan-verification"
            data = (
                self._request(
                    "GET", path, params={key: value for key, value in body.items() if key != "format"}, intent=intent
                )
                if read_only
                else self._request("POST", path, body=body, intent=intent)
            )
            result = verification_protocol.summary(
                data,
                body=body,
                context=source["context"],
                chart_artifact_id=chart_artifact_id,
                chart_artifact_digest=chart_artifact_digest,
            )
            if (
                self._install_plan_verification_source(job_id, intent, body, chart_artifact_id, chart_artifact_digest)
                != source
            ):
                raise ValueError
            return result
        except (ContractError, KeyError, ValueError, TypeError, OverflowError):
            raise _invalid_response() from None

    def prepare_install_plan_verification(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_verification(
            job_id,
            intent=intent,
            body=body,
            read_only=False,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

    def install_plan_verification(
        self, job_id: str, *, intent: str, body: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
    ) -> dict[str, Any]:
        return self._install_plan_verification(
            job_id,
            intent=intent,
            body=body,
            read_only=True,
            chart_artifact_id=chart_artifact_id,
            chart_artifact_digest=chart_artifact_digest,
        )

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
