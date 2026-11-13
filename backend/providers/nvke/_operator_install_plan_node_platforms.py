"""Closed node/image platform observations without native admission."""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any

from nvke._operator_install_plan import decimal, integer
from nvke._operator_install_plan_immutable import BASE
from nvke._operator_install_plan_immutable import capability as capability
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

STATE = "requires-attested-cpu-importer-scheduling-credentials-availability-phase-use-and-linked-admission"
PROFILES = {
    "containerd/v2.3.0",
    "containerd/v2.3.4",
    "containerd/v2.3.7",
    "containerd/v2.2.3",
    "containerd/v2.1.7",
    "containerd/v1.7.31",
}
NAME = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*\Z")
UID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
BYTE_LIMITS = {
    "maximumImageBytes": 2**63 - 1,
    "maximumMetadataBytes": 64 * 1024 * 1024,
    "maximumBytes": 16 * 1024 * 1024,
    "maximumObservationBytes": 64 * 1024 * 1024,
    "maximumHelperBytes": 2**63 - 1,
}
INTEGER_LIMITS = {"maximumCalls": 65536, "maximumPairs": 100000, "timeoutSeconds": 300}
COUNTS = {"nodeCount", "contentCount", "imageReferenceCount", "pairCount", "issueCount"}


def selections(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not 1 <= len(value) <= 10000:
        raise ValueError
    uids = set()
    for name, row in value.items():
        if (
            not isinstance(name, str)
            or len(name) > 253
            or not NAME.fullmatch(name)
            or not isinstance(row, dict)
            or set(row) != {"uid", "runtimeProfile", "platform"}
            or not isinstance(row["uid"], str)
            or not UID.fullmatch(row["uid"])
            or row["uid"] in uids
            or not isinstance(row["runtimeProfile"], str)
            or row["runtimeProfile"] not in PROFILES
        ):
            raise ValueError
        uids.add(row["uid"])
        platform = row["platform"]
        if (
            not isinstance(platform, dict)
            or not {"os", "architecture"} <= platform.keys()
            or platform.keys() - {"os", "architecture", "variant", "os.version", "os.features"}
            or any(not isinstance(platform[k], str) or not platform[k] for k in ("os", "architecture"))
            or any(not isinstance(platform[k], str) for k in ("variant", "os.version") if k in platform)
            or (
                "os.features" in platform
                and (
                    not isinstance(platform["os.features"], list)
                    or any(not isinstance(v, str) for v in platform["os.features"])
                )
            )
        ):
            raise ValueError
    return copy.deepcopy(value)


def request(body: Any, *, job_id: str, chart_artifact_id: str, workload_id: str, read_only: bool) -> dict[str, Any]:
    fields = BASE if read_only else BASE | {"nodePlatforms", *BYTE_LIMITS, *INTEGER_LIMITS}
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
    if not read_only:
        selected = selections(body["nodePlatforms"])
        for key, limit in BYTE_LIMITS.items():
            if not 1 <= int(decimal(body[key])) <= limit:
                raise ValueError
        for key, limit in INTEGER_LIMITS.items():
            integer(body[key], 1, limit)
        if body["maximumCalls"] < 4 * len(selected) + 4:
            raise ValueError
    return copy.deepcopy(body)


def summary(data: Any, *, body: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        "artifactId",
        "artifactDigest",
        "sourceDigest",
        "captureDigest",
        "platformDigest",
        "observedAt",
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
    for key in ("artifactDigest", "sourceDigest", "captureDigest", "platformDigest"):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    for key in COUNTS:
        integer(data[key], 0, 2**63 - 1)
    if (
        not 1 <= data["nodeCount"] <= 10000
        or data["imageReferenceCount"] != source["binding"]["imageCount"]
        or data["contentCount"] > data["imageReferenceCount"]
        or data["pairCount"] != data["nodeCount"] * data["contentCount"]
        or data["pairCount"] > 100000
        or ("nodePlatforms" in body and data["nodeCount"] != len(body["nodePlatforms"]))
        or ("maximumPairs" in body and data["pairCount"] > body["maximumPairs"])
        or datetime.fromisoformat(timestamp(data["observedAt"]))
        < datetime.fromisoformat(timestamp(source["binding"]["observedAt"]))
    ):
        raise ValueError
    return copy.deepcopy(data)
