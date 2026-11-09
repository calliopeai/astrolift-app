"""Bounded render-context requests and source-bound, private-data-free replies."""

from __future__ import annotations

from typing import Any

from nvke._operator_install_plan import ITEM_NAME, decimal, integer
from nvke._operator_install_plan_charts import capability as capability
from nvke._operator_install_review import SHA, timestamp
from nvke.operator_contract import identifier

STATE = "requires-authenticated-phase-verification-images-and-linked-admission"
BINDING_FIELDS = ("artifactId", "planId", "planDigest", "treeId", "treeDigest", "item")
WRITE_FIELDS = {
    "maximumBytes",
    "maximumArtifactBytes",
    "revision",
    "upgrade",
    "maximumCalls",
    "maximumIterations",
    "timeoutSeconds",
}


def request(body: Any, *, job_id: str, read_only: bool, chart_artifact_id: str) -> dict[str, Any]:
    required = {"format", *BINDING_FIELDS} | (set() if read_only else WRITE_FIELDS)
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError
    integer(body["format"], 1, 1)
    identities = [
        identifier(value) for value in (job_id, chart_artifact_id, body["artifactId"], body["planId"], body["treeId"])
    ]
    if len(set(identities)) != 5:
        raise ValueError
    for field in ("planDigest", "treeDigest"):
        if not isinstance(body[field], str) or not SHA.fullmatch(body[field]):
            raise ValueError
    if not isinstance(body["item"], str) or not ITEM_NAME.fullmatch(body["item"]):
        raise ValueError
    if not read_only:
        for field, minimum, maximum in (
            ("maximumBytes", 1, 512 * 1024 * 1024),
            ("maximumArtifactBytes", 65536, 128 * 1024 * 1024),
            ("revision", 1, 2**63 - 1),
        ):
            if not minimum <= int(decimal(body[field])) <= maximum:
                raise ValueError
        for field, maximum in (("maximumCalls", 2048), ("maximumIterations", 64), ("timeoutSeconds", 300)):
            integer(body[field], 1, maximum)
        if type(body["upgrade"]) is not bool or (not body["upgrade"] and body["revision"] != "1"):
            raise ValueError
    return dict(body)


def summary(data: Any, *, body: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        *BINDING_FIELDS,
        "ordinal",
        "captureDigest",
        "frozenChartSha256",
        "contextDigest",
        "documentCount",
        "lookupCount",
        "observedAt",
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    integer(data["ordinal"], 0, 9999)
    integer(data["documentCount"], 0, 10000)
    integer(data["lookupCount"], 0, 2048)
    if (
        any(data[field] != body[field] for field in BINDING_FIELDS)
        or item["name"] != body["item"]
        or item["type"] != "helm"
        or data["ordinal"] != item["ordinal"]
        or data["executionState"] != STATE
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    for field in ("captureDigest", "frozenChartSha256", "contextDigest"):
        if not isinstance(data[field], str) or not SHA.fullmatch(data[field]):
            raise ValueError
    timestamp(data["observedAt"])
    return dict(data)
