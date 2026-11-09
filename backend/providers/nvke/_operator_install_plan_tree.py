"""Strict execution-tree requests and private-data-free, non-authorizing replies."""

from __future__ import annotations

from typing import Any

from nvke._operator_install_plan import decimal, integer
from nvke._operator_install_plan_charts import capability as capability
from nvke._operator_install_review import SHA
from nvke.operator_contract import identifier

STATE = "requires-native-rendering-images-and-live-verification"
BINDING_FIELDS = ("artifactId", "planId", "planDigest", "chartArtifactId", "chartArtifactDigest")


def request(body: Any, *, job_id: str, read_only: bool) -> dict[str, Any]:
    required = {"format", *BINDING_FIELDS}
    if not read_only:
        required.add("maximumBytes")
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError
    integer(body["format"], 1, 1)
    identities = [identifier(value) for value in (job_id, body["artifactId"], body["planId"], body["chartArtifactId"])]
    if len(set(identities)) != 4:
        raise ValueError
    for field in ("planDigest", "chartArtifactDigest"):
        if not isinstance(body[field], str) or not SHA.fullmatch(body[field]):
            raise ValueError
    if not read_only and decimal(body["maximumBytes"]) == "0":
        raise ValueError
    return dict(body)


def summary(data: Any, *, body: dict[str, Any], chart_bytes: str, chart_count: int) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        *BINDING_FIELDS,
        "treeDigest",
        "fileCount",
        "bytes",
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    count = integer(data["fileCount"], 1, 100000)
    integer(chart_count, 0, 10000)
    if (
        any(data[field] != body[field] for field in BINDING_FIELDS)
        or not isinstance(data["treeDigest"], str)
        or not SHA.fullmatch(data["treeDigest"])
        or data["executionState"] != STATE
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    size = int(decimal(data["bytes"]))
    if (
        size == 0
        or size < int(decimal(chart_bytes))
        or count < chart_count
        or ("maximumBytes" in body and size > int(body["maximumBytes"]))
    ):
        raise ValueError
    return dict(data)
