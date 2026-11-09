"""Closed render-verification requests and metadata-only historical replies."""

from __future__ import annotations

from typing import Any

from nvke._operator_install_plan import ITEM_NAME, decimal, integer
from nvke._operator_install_plan_charts import capability as capability
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

STATE = "requires-phase-use-resource-credential-schema-images-and-linked-admission"
BINDING_FIELDS = ("artifactId", "contextId", "contextDigest", "planId", "planDigest", "treeId", "treeDigest", "item")
WRITE_FIELDS = {"maximumBytes", "maximumCaptureAgeSeconds", "maximumCalls", "timeoutSeconds"}


def request(body: Any, *, job_id: str, chart_artifact_id: str, read_only: bool) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {"format", *BINDING_FIELDS} | (set() if read_only else WRITE_FIELDS):
        raise ValueError
    integer(body["format"], 1, 1)
    ids = [
        identifier(value)
        for value in (job_id, chart_artifact_id, body["artifactId"], body["contextId"], body["planId"], body["treeId"])
    ]
    if len(set(ids)) != 6:
        raise ValueError
    for key in ("contextDigest", "planDigest", "treeDigest"):
        if not isinstance(body[key], str) or not SHA.fullmatch(body[key]):
            raise ValueError
    if not isinstance(body["item"], str) or not ITEM_NAME.fullmatch(body["item"]):
        raise ValueError
    if not read_only:
        if not 1 <= int(decimal(body["maximumBytes"])) <= 512 * 1024 * 1024:
            raise ValueError
        for key, maximum in (("maximumCaptureAgeSeconds", 3600), ("maximumCalls", 2048), ("timeoutSeconds", 300)):
            integer(body[key], 1, maximum)
    return dict(body)


def summary(
    data: Any, *, body: dict[str, Any], context: dict[str, Any], chart_artifact_id: str, chart_artifact_digest: str
) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        *BINDING_FIELDS,
        "artifactDigest",
        "chartArtifactId",
        "chartArtifactDigest",
        "ordinal",
        "captureDigest",
        "frozenChartSha256",
        "verificationDigest",
        "observedAt",
        "requests",
        "bytes",
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    integer(data["ordinal"], 0, 9999)
    calls = integer(data["requests"], 1, 2048)
    size = int(decimal(data["bytes"]))
    if (
        not 1 <= size <= 512 * 1024 * 1024
        or ("maximumBytes" in body and size > int(body["maximumBytes"]))
        or ("maximumCalls" in body and calls > body["maximumCalls"])
    ):
        raise ValueError
    if (
        any(data[key] != body[key] for key in BINDING_FIELDS)
        or data["chartArtifactId"] != chart_artifact_id
        or data["chartArtifactDigest"] != chart_artifact_digest
        or data["executionState"] != STATE
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    for key, context_key in (
        ("contextId", "artifactId"),
        ("contextDigest", "contextDigest"),
        ("planId", "planId"),
        ("planDigest", "planDigest"),
        ("treeId", "treeId"),
        ("treeDigest", "treeDigest"),
        ("item", "item"),
        ("ordinal", "ordinal"),
        ("captureDigest", "captureDigest"),
        ("frozenChartSha256", "frozenChartSha256"),
    ):
        if data[key] != context[context_key]:
            raise ValueError
    for key in ("artifactDigest", "chartArtifactDigest", "captureDigest", "frozenChartSha256", "verificationDigest"):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    timestamp(data["observedAt"])
    return dict(data)
