"""Closed workload-inventory requests and non-authorizing public observations."""

from __future__ import annotations

import copy
from typing import Any

from nvke._operator_install_plan import ITEM_NAME, decimal, integer
from nvke._operator_install_plan_charts import capability as capability
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

STATE = "requires-reviewed-operator-images-platform-credentials-rewrite-import-use-and-linked-admission"
BASE = {"format", "artifactId", "planId", "planDigest", "treeId", "treeDigest"}
WRITE_FIELDS = {"contextBindings", "maximumBytes", "maximumResources", "timeoutSeconds"}
COUNTS = {"itemCount", "resourceCount", "imageUseCount", "imageReferenceCount", "issueCount"}


def request(body: Any, *, job_id: str, chart_artifact_id: str, read_only: bool) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != BASE | (set() if read_only else WRITE_FIELDS):
        raise ValueError
    integer(body["format"], 1, 1)
    ids = {
        identifier(value) for value in (job_id, chart_artifact_id, body["artifactId"], body["planId"], body["treeId"])
    }
    if len(ids) != 5:
        raise ValueError
    for key in ("planDigest", "treeDigest"):
        if not isinstance(body[key], str) or not SHA.fullmatch(body[key]):
            raise ValueError
    if not read_only:
        if not 1 <= int(decimal(body["maximumBytes"])) <= 64 * 1024 * 1024:
            raise ValueError
        integer(body["maximumResources"], 1, 10000)
        integer(body["timeoutSeconds"], 1, 300)
        bindings = body["contextBindings"]
        if not isinstance(bindings, dict) or len(bindings) > 10000:
            raise ValueError
        for name, row in bindings.items():
            if (
                not isinstance(name, str)
                or not ITEM_NAME.fullmatch(name)
                or not isinstance(row, dict)
                or set(row) != {"contextId", "contextDigest"}
                or not isinstance(row["contextDigest"], str)
                or not SHA.fullmatch(row["contextDigest"])
            ):
                raise ValueError
            context_id = identifier(row["contextId"])
            if context_id in ids:
                raise ValueError
            ids.add(context_id)
    return copy.deepcopy(body)


def summary(data: Any, *, body: dict[str, Any], item_count: int) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        "artifactId",
        "artifactDigest",
        "sourceDigest",
        "captureDigest",
        "workloadDigest",
        "observedAt",
        *COUNTS,
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    for key in COUNTS:
        integer(data[key], 0, 2**63 - 1)
    if (
        data["artifactId"] != body["artifactId"]
        or data["itemCount"] != item_count
        or data["resourceCount"] > body.get("maximumResources", 10000)
        or data["imageReferenceCount"] > data["imageUseCount"]
        or data["executionState"] != STATE
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    for key in ("artifactDigest", "sourceDigest", "captureDigest", "workloadDigest"):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    timestamp(data["observedAt"])
    return dict(data)
