"""Closed signed workload/image selections and non-authorizing observations."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from nvke._operator_install_plan import decimal, integer
from nvke._operator_install_plan_charts import capability as capability
from nvke._operator_install_plan_workload import BASE as WORKLOAD_BASE
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

STATE = "requires-reviewed-operator-images-node-platform-credentials-rewrite-import-use-and-linked-admission"
BASE = WORKLOAD_BASE | {"workloadId", "workloadDigest"}
WRITE_FIELDS = {"imageBindings", "maximumImageBytes", "maximumBytes", "timeoutSeconds"}
COUNTS = {"imageCount", "imageUseCount", "importCount", "rewriteCount", "workloadIssueCount"}


def request(body: Any, *, job_id: str, chart_artifact_id: str, read_only: bool) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != BASE | (set() if read_only else WRITE_FIELDS):
        raise ValueError
    integer(body["format"], 1, 1)
    ids = [
        identifier(value)
        for value in (
            job_id,
            chart_artifact_id,
            body["artifactId"],
            body["planId"],
            body["treeId"],
            body["workloadId"],
        )
    ]
    if len(set(ids)) != 6 or any(
        not isinstance(body[key], str) or not SHA.fullmatch(body[key])
        for key in ("planDigest", "treeDigest", "workloadDigest")
    ):
        raise ValueError
    if not read_only:
        if not 1 <= int(decimal(body["maximumBytes"])) <= 64 * 1024 * 1024:
            raise ValueError
        if not 1 <= int(decimal(body["maximumImageBytes"])) < 2**63:
            raise ValueError
        integer(body["timeoutSeconds"], 1, 300)
        bindings = body["imageBindings"]
        if not isinstance(bindings, dict) or len(bindings) > 10000:
            raise ValueError
        for reference, row in bindings.items():
            if (
                not isinstance(reference, str)
                or not 1 <= len(reference) <= 4096
                or "://" in reference
                or any(ord(char) <= 32 or ord(char) >= 127 or char in "?#\\" for char in reference)
                or not isinstance(row, dict)
                or set(row) != {"sourceKind", "artifactId", "artifactDigest"}
                or row["sourceKind"] not in {"local", "registry"}
                or identifier(row["artifactId"]) in ids
                or not isinstance(row["artifactDigest"], str)
                or not SHA.fullmatch(row["artifactDigest"])
            ):
                raise ValueError
    return copy.deepcopy(body)


def summary(data: Any, *, body: dict[str, Any], workload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        "artifactId",
        "artifactDigest",
        "sourceDigest",
        "captureDigest",
        "bindingDigest",
        "observedAt",
        *COUNTS,
        "verifiedImageBytes",
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    for key in COUNTS:
        integer(data[key], 0, 2**63 - 1)
    size = int(decimal(data["verifiedImageBytes"]))
    if (
        data["artifactId"] != body["artifactId"]
        or data["imageCount"] > 10000
        or data["imageCount"] < workload["imageReferenceCount"]
        or ("imageBindings" in body and data["imageCount"] != len(body["imageBindings"]))
        or data["imageUseCount"] != workload["imageUseCount"]
        or data["workloadIssueCount"] != workload["issueCount"]
        or data["rewriteCount"] > data["imageCount"]
        or data["importCount"] > 10000
        or (data["imageCount"] == 0) != (size == 0)
        or ("maximumImageBytes" in body and size > int(body["maximumImageBytes"]))
        or data["executionState"] != STATE
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    for key in ("artifactDigest", "sourceDigest", "captureDigest", "bindingDigest"):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    observed = timestamp(data["observedAt"])
    if datetime.fromisoformat(observed) < datetime.fromisoformat(timestamp(workload["observedAt"])):
        raise ValueError
    return dict(data)
