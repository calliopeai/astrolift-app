"""Closed immutable-payload and sealed-tree controls, without admission."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from nvke._operator_install_plan import ITEM_NAME, decimal, integer
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

BASE = {"format", "artifactId", "planId", "planDigest", "treeId", "treeDigest", "bindingId", "bindingDigest"}
PAYLOAD_STATE = "requires-reviewed-operator-images-platform-credentials-import-use-and-linked-admission"
TREE_STATE = "requires-fresh-phase-platform-credential-import-use-and-linked-admission"


def capability(value: Any, *, read_only: bool) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "prepare", "readOnly", "continuation"}
        or type(value["format"]) is not int
        or value["format"] != 1
        or type(value["prepare"]) is not bool
        or (not read_only and value["prepare"] is not True)
        or value["readOnly"] is not True
        or value["continuation"] is not False
    ):
        raise ValueError


def selections(value: Any, *, reserved: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not 1 <= len(value) <= 10000:
        raise ValueError
    ids = set(reserved)
    for name, row in value.items():
        if (
            not isinstance(name, str)
            or not ITEM_NAME.fullmatch(name)
            or not isinstance(row, dict)
            or set(row) != {"artifactId", "artifactDigest"}
            or not isinstance(row["artifactDigest"], str)
            or not SHA.fullmatch(row["artifactDigest"])
        ):
            raise ValueError
        artifact_id = identifier(row["artifactId"])
        if artifact_id in ids:
            raise ValueError
        ids.add(artifact_id)
    return copy.deepcopy(value)


def request(
    body: Any, *, job_id: str, chart_artifact_id: str, workload_id: str, read_only: bool, tree: bool
) -> dict[str, Any]:
    fields = BASE | (set() if tree else {"item"})
    if not read_only:
        fields |= {"maximumBytes", "timeoutSeconds"} | ({"payloadBindings"} if tree else {"maximumResources"})
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError
    integer(body["format"], 1, 1)
    ids = [
        identifier(v)
        for v in (
            job_id,
            chart_artifact_id,
            workload_id,
            body["artifactId"],
            body["planId"],
            body["treeId"],
            body["bindingId"],
        )
    ]
    if len(set(ids)) != 7 or any(
        not isinstance(body[k], str) or not SHA.fullmatch(body[k])
        for k in ("planDigest", "treeDigest", "bindingDigest")
    ):
        raise ValueError
    if not tree and (not isinstance(body["item"], str) or not ITEM_NAME.fullmatch(body["item"])):
        raise ValueError
    if not read_only:
        size = int(decimal(body["maximumBytes"]))
        if not 1 <= size <= (2**63 - 1 if tree else 64 * 1024 * 1024):
            raise ValueError
        integer(body["timeoutSeconds"], 1, 300)
        if tree:
            selections(body["payloadBindings"], reserved=set(ids))
        else:
            integer(body["maximumResources"], 1, 10000)
    return copy.deepcopy(body)


def summary(data: Any, *, body: dict[str, Any], source: dict[str, Any], tree: bool) -> dict[str, Any]:
    counts = (
        {"fileCount", "phaseCount", "skippedPhaseCount", "importCount"}
        if tree
        else {"fileCount", "resourceCount", "imageUseCount", "rewriteCount"}
    )
    size_field, digest_field = ("bytes", "executionDigest") if tree else ("payloadBytes", "payloadDigest")
    if not isinstance(data, dict) or set(data) != {
        "format",
        "artifactId",
        "artifactDigest",
        "sourceDigest",
        "captureDigest",
        digest_field,
        "observedAt",
        "sourceObservedAt",
        size_field,
        *counts,
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    if (
        data["artifactId"] != body["artifactId"]
        or data["continuationAuthorized"] is not False
        or data["executionState"] != (TREE_STATE if tree else PAYLOAD_STATE)
    ):
        raise ValueError
    for key in ("artifactDigest", "sourceDigest", "captureDigest", digest_field):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    for key in counts:
        integer(data[key], 0, 2**63 - 1)
    size = int(decimal(data[size_field]))
    if "maximumBytes" in body and size > int(body["maximumBytes"]):
        raise ValueError
    if not 1 <= data["fileCount"] <= (100000 if tree else 10000):
        raise ValueError
    source_time = timestamp(data["sourceObservedAt"])
    if source_time != timestamp(source["binding"]["observedAt"]) or datetime.fromisoformat(
        timestamp(data["observedAt"])
    ) < datetime.fromisoformat(source_time):
        raise ValueError
    if tree:
        if size == 0:
            raise ValueError
        skipped = source["metadata"]["plan"]["firstRemainingOrdinal"]
        if (
            data["skippedPhaseCount"] != skipped
            or data["phaseCount"] != source["metadata"]["itemCount"] - skipped
            or data["importCount"] > source["binding"]["imageCount"]
        ):
            raise ValueError
    elif (
        (data["resourceCount"] > 0 and size == 0)
        or (data["imageUseCount"] > 0 and data["resourceCount"] == 0)
        or data["resourceCount"] > source["workload"]["resourceCount"]
        or data["imageUseCount"] > source["binding"]["imageUseCount"]
        or data["rewriteCount"] > data["imageUseCount"]
        or ("maximumResources" in body and data["resourceCount"] > body["maximumResources"])
    ):
        raise ValueError
    return dict(data)
