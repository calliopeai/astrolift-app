"""Closed admitted-Pod pull observations; no credential or execution authority."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from nvke._operator_install_plan import decimal, integer
from nvke._operator_install_plan_immutable import BASE
from nvke._operator_install_plan_immutable import capability as capability
from nvke._operator_install_plan_node_platforms import NAME, UID
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

STATE = "requires-pod-ownership-kubelet-credential-registry-correspondence-and-linked-admission"
COUNTS = {"podCount", "serviceAccountCount", "secretCount", "missingReferenceCount"}


def selections(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not 1 <= len(value) <= 10000:
        raise ValueError
    for key, uid in value.items():
        if not isinstance(key, str) or key.count("/") != 1 or not isinstance(uid, str) or not UID.fullmatch(uid):
            raise ValueError
        namespace, name = key.split("/")
        if (
            not 1 <= len(namespace) <= 63
            or "." in namespace
            or not NAME.fullmatch(namespace)
            or not 1 <= len(name) <= 253
            or not NAME.fullmatch(name)
        ):
            raise ValueError
    if len(set(value.values())) != len(value):
        raise ValueError
    return copy.deepcopy(value)


def request(body: Any, *, job_id: str, chart_artifact_id: str, workload_id: str, read_only: bool) -> dict[str, Any]:
    fields = BASE if read_only else BASE | {"selectedPods", "maximumBytes", "maximumCalls", "timeoutSeconds"}
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
        not isinstance(body[key], str) or not SHA.fullmatch(body[key])
        for key in ("planDigest", "treeDigest", "bindingDigest")
    ):
        raise ValueError
    if not read_only:
        selected = selections(body["selectedPods"])
        if not 1 <= int(decimal(body["maximumBytes"])) <= 64 * 1024 * 1024:
            raise ValueError
        integer(body["maximumCalls"], 1, 32768)
        integer(body["timeoutSeconds"], 1, 300)
        if body["maximumCalls"] < 2 * len(selected) + 2:
            raise ValueError
    return copy.deepcopy(body)


def summary(data: Any, *, body: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        "artifactId",
        "artifactDigest",
        "sourceDigest",
        "captureDigest",
        "authorityDigest",
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
    for key in ("artifactDigest", "sourceDigest", "captureDigest", "authorityDigest"):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    for key in COUNTS:
        integer(data[key], 0, 32768)
    minimum_calls = 2 * (data["podCount"] + data["serviceAccountCount"] + data["secretCount"]) + 2
    if (
        not 1 <= data["podCount"] <= 10000
        or data["serviceAccountCount"] > data["podCount"]
        or data["missingReferenceCount"] > data["serviceAccountCount"] + data["secretCount"]
        or minimum_calls > 32768
        or ("selectedPods" in body and data["podCount"] != len(body["selectedPods"]))
        or ("maximumCalls" in body and minimum_calls > body["maximumCalls"])
        or datetime.fromisoformat(timestamp(data["observedAt"]))
        < datetime.fromisoformat(timestamp(source["binding"]["observedAt"]))
    ):
        raise ValueError
    return copy.deepcopy(data)
