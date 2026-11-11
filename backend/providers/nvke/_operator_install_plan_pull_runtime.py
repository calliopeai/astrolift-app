"""Read-only private-capture correspondence; public counts and digests only."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from nvke import _operator_install_plan_pull_secret_analysis as analyses
from nvke._operator_install_plan import integer
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

BASE = analyses.BASE | {"analysisDigest", "nodePlatformId", "nodePlatformDigest"}
STATE = (
    "requires-workload-ownership-scheduling-node-keyring-plugins-registry-availability-fresh-use-and-linked-admission"
)


def capability(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"format", "readOnly", "continuation"}:
        raise ValueError
    integer(value["format"], 1, 1)
    if value["readOnly"] is not True or value["continuation"] is not False:
        raise ValueError


def request(body: Any, *, job_id: str, chart_artifact_id: str, workload_id: str) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != BASE:
        raise ValueError
    analyses.request(
        {key: body[key] for key in analyses.BASE},
        job_id=job_id,
        chart_artifact_id=chart_artifact_id,
        workload_id=workload_id,
        read_only=True,
    )
    node_id = identifier(body["nodePlatformId"])
    if node_id in {
        job_id,
        chart_artifact_id,
        workload_id,
        *(body[key] for key in ("artifactId", "planId", "treeId", "bindingId", "pullAuthorityId")),
    }:
        raise ValueError
    for key in ("analysisDigest", "nodePlatformDigest"):
        if not isinstance(body[key], str) or not SHA.fullmatch(body[key]):
            raise ValueError
    return copy.deepcopy(body)


def summary(data: Any, *, body: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        "analysisArtifactId",
        "analysisArtifactDigest",
        "nodePlatformArtifactId",
        "nodePlatformArtifactDigest",
        "correspondenceDigest",
        "analysisCaptureDigest",
        "pullCaptureDigest",
        "nodePlatformCaptureDigest",
        "imageUseCount",
        "nodeCount",
        "sourceObservedAt",
        "nodeObservedAt",
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    expected = {
        "analysisArtifactId": body["artifactId"],
        "analysisArtifactDigest": body["analysisDigest"],
        "nodePlatformArtifactId": body["nodePlatformId"],
        "nodePlatformArtifactDigest": body["nodePlatformDigest"],
        "analysisCaptureDigest": source["analysis"]["captureDigest"],
        "pullCaptureDigest": source["pullAuthority"]["captureDigest"],
        "nodePlatformCaptureDigest": source["nodePlatforms"]["captureDigest"],
        "sourceObservedAt": source["analysis"]["sourceObservedAt"],
        "imageUseCount": source["analysis"]["imageUseCount"],
        "executionState": STATE,
    }
    if any(data[key] != value for key, value in expected.items()) or data["continuationAuthorized"] is not False:
        raise ValueError
    for key in (
        "analysisArtifactDigest",
        "nodePlatformArtifactDigest",
        "correspondenceDigest",
        "analysisCaptureDigest",
        "pullCaptureDigest",
        "nodePlatformCaptureDigest",
    ):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    integer(data["imageUseCount"], 1, 1024)
    integer(data["nodeCount"], 1, min(data["imageUseCount"], source["nodePlatforms"]["nodeCount"]))
    if (
        not datetime.fromisoformat(timestamp(source["binding"]["observedAt"]))
        <= datetime.fromisoformat(timestamp(data["nodeObservedAt"]))
        <= datetime.fromisoformat(timestamp(source["nodePlatforms"]["observedAt"]))
    ):
        raise ValueError
    timestamp(data["sourceObservedAt"])
    return copy.deepcopy(data)
