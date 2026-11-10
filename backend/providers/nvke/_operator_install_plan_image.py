"""Closed registry/local image requests and non-authorizing summaries."""

from __future__ import annotations

import re
from typing import Any

from nvke._operator_install_plan import ITEM_NAME, decimal, integer
from nvke._operator_install_plan_charts import capability as capability
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

STATE = "requires-workload-platform-credential-phase-use-and-linked-admission"
BASE = {"format", "artifactId", "planId", "planDigest", "treeId", "treeDigest", "reference", "policy"}
WRITE_FIELDS = {"maximumBytes", "maximumCalls", "timeoutSeconds"}
LOCAL_BASE = (BASE - {"policy"}) | {"sourceKind"}
LOCAL_WRITE_FIELDS = {"maximumBytes", "timeoutSeconds"}
WORKLOAD = {"workloadId", "workloadDigest"}
DIGEST = re.compile(r"(?:sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})\Z")


def capability_name(body: dict[str, Any]) -> str:
    if body.get("sourceKind") == "local":
        return "installPlanLocalImage"
    return "installPlanWorkloadImage" if "workloadId" in body else "installPlanImage"


def read_fields(body: dict[str, Any]) -> set[str]:
    if body.get("sourceKind") == "local":
        return LOCAL_BASE
    return BASE | (WORKLOAD if "workloadId" in body else set())


def request(body: Any, *, job_id: str, chart_artifact_id: str, read_only: bool) -> dict[str, Any]:
    local = isinstance(body, dict) and body.get("sourceKind") == "local"
    fields, limits = (LOCAL_BASE, LOCAL_WRITE_FIELDS) if local else (BASE, WRITE_FIELDS)
    linked = isinstance(body, dict) and bool(set(body) & WORKLOAD)
    if linked and not local:
        fields = fields | WORKLOAD
    if not isinstance(body, dict) or set(body) != fields | (set() if read_only else limits):
        raise ValueError
    integer(body["format"], 1, 1)
    ids = [
        identifier(value) for value in (job_id, chart_artifact_id, body["artifactId"], body["planId"], body["treeId"])
    ]
    if linked:
        ids.append(identifier(body["workloadId"]))
        if not isinstance(body["workloadDigest"], str) or not SHA.fullmatch(body["workloadDigest"]):
            raise ValueError
    if len(set(ids)) != (6 if linked else 5):
        raise ValueError
    if any(not isinstance(body[key], str) or not SHA.fullmatch(body[key]) for key in ("planDigest", "treeDigest")):
        raise ValueError
    if not local and (not isinstance(body["policy"], str) or not ITEM_NAME.fullmatch(body["policy"])):
        raise ValueError
    reference = body["reference"]
    if (
        not isinstance(reference, str)
        or not 1 <= len(reference) <= 4096
        or "://" in reference
        or any(ord(char) <= 32 or ord(char) >= 127 or char in "?#\\" for char in reference)
    ):
        raise ValueError
    if local and (reference.count("@") != 1 or not DIGEST.fullmatch(reference.partition("@")[2])):
        raise ValueError
    if not read_only:
        if not 1 <= int(decimal(body["maximumBytes"])) < 2**63:
            raise ValueError
        if not local:
            integer(body["maximumCalls"], 1, 4096)
        integer(body["timeoutSeconds"], 1, 300)
    return dict(body)


def summary(data: Any, *, body: dict[str, Any]) -> dict[str, Any]:
    local = body.get("sourceKind") == "local"
    linked = "workloadId" in body
    if not isinstance(data, dict) or set(data) != (
        {
            "format",
            "artifactId",
            "artifactDigest",
            "sourceDigest",
            "captureDigest",
            "observedAt",
            "rootDigest",
            "bytes",
            "blobCount",
            "executionState",
            "continuationAuthorized",
        }
        | ({"sourceKind"} if local else set())
        | ({"sourceKind", *WORKLOAD} if linked else set())
    ):
        raise ValueError
    if local and (data["sourceKind"] != "local" or data["rootDigest"] != body["reference"].partition("@")[2]):
        raise ValueError
    if linked and (data["sourceKind"] != "registry" or any(data[key] != body[key] for key in WORKLOAD)):
        raise ValueError
    integer(data["format"], 1, 1)
    integer(data["blobCount"], 1, 100000)
    size = int(decimal(data["bytes"]))
    if (
        not 1 <= size < 2**63
        or ("maximumBytes" in body and size > int(body["maximumBytes"]))
        or data["artifactId"] != body["artifactId"]
        or data["executionState"] != STATE
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    for key in ("artifactDigest", "sourceDigest", "captureDigest"):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    if not isinstance(data["rootDigest"], str) or not DIGEST.fullmatch(data["rootDigest"]):
        raise ValueError
    timestamp(data["observedAt"])
    return dict(data)
