"""Closed private-keyring interpretation controls; public metadata only."""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any

from nvke import _operator_install_plan_pull_authority as parent
from nvke._operator_install_plan import decimal, integer
from nvke._operator_install_plan_immutable import BASE as IMMUTABLE_BASE
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

capability = parent.capability
BASE = IMMUTABLE_BASE | {"pullAuthorityId", "pullAuthorityDigest"}
STATE = "requires-pod-ownership-kubelet-configuration-plugin-registry-correspondence-and-linked-admission"
COUNTS = {"imageUseCount", "candidateCount", "missingReferenceCount"}
POINTER = re.compile(r"/spec/(containers|initContainers|ephemeralContainers)/(0|[1-9][0-9]{0,5})/image\Z")


def selections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 1024:
        raise ValueError
    seen = set()
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) != {"podKey", "imagePointer", "ensureSecretPulledImages"}
            or not isinstance(row["podKey"], str)
            or not isinstance(row["imagePointer"], str)
            or not POINTER.fullmatch(row["imagePointer"])
            or type(row["ensureSecretPulledImages"]) is not bool
            or (row["podKey"], row["imagePointer"]) in seen
        ):
            raise ValueError
        parent.selections({row["podKey"]: "selection-placeholder"})
        seen.add((row["podKey"], row["imagePointer"]))
    return copy.deepcopy(value)


def request(body: Any, *, job_id: str, chart_artifact_id: str, workload_id: str, read_only: bool) -> dict[str, Any]:
    fields = BASE if read_only else BASE | {"imageSelections", "maximumBytes", "maximumHelperBytes", "timeoutSeconds"}
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError
    parent.request(
        {key: value for key, value in body.items() if key in IMMUTABLE_BASE},
        job_id=job_id,
        chart_artifact_id=chart_artifact_id,
        workload_id=workload_id,
        read_only=True,
    )
    capture_id = identifier(body["pullAuthorityId"])
    if (
        capture_id
        in {
            job_id,
            chart_artifact_id,
            workload_id,
            body["artifactId"],
            body["planId"],
            body["treeId"],
            body["bindingId"],
        }
        or not isinstance(body["pullAuthorityDigest"], str)
        or not SHA.fullmatch(body["pullAuthorityDigest"])
    ):
        raise ValueError
    if not read_only:
        selections(body["imageSelections"])
        maximum = int(decimal(body["maximumBytes"]))
        helper = int(decimal(body["maximumHelperBytes"]))
        if not 1 <= helper <= maximum <= 64 * 1024 * 1024:
            raise ValueError
        integer(body["timeoutSeconds"], 1, 300)
    return copy.deepcopy(body)


def summary(data: Any, *, body: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        "artifactId",
        "artifactDigest",
        "sourceDigest",
        "captureDigest",
        "authorityDigest",
        "analyzedAt",
        "sourceObservedAt",
        *COUNTS,
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    if (
        data["artifactId"] != body["artifactId"]
        or data["executionState"] != STATE
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    for key in ("artifactDigest", "sourceDigest", "captureDigest", "authorityDigest"):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    integer(data["imageUseCount"], 1, 1024)
    integer(data["candidateCount"], 0, 1024 * 100000)
    integer(data["missingReferenceCount"], 0, 1024 * 10000)
    if (
        data["candidateCount"] > data["imageUseCount"] * 100000
        or data["missingReferenceCount"] > data["imageUseCount"] * 10000
        or ("imageSelections" in body and data["imageUseCount"] != len(body["imageSelections"]))
    ):
        raise ValueError
    # The raw capture time precedes its signed publication time. Interpretation
    # time is distinct; historical review must never refresh either timestamp.
    times = [
        datetime.fromisoformat(timestamp(value))
        for value in (
            source["binding"]["observedAt"],
            data["sourceObservedAt"],
            source["pullAuthority"]["observedAt"],
            data["analyzedAt"],
        )
    ]
    if times != sorted(times):
        raise ValueError
    return copy.deepcopy(data)
