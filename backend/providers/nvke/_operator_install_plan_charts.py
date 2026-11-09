"""Bounded chart-stage requests and redacted, non-authorizing summaries."""

from __future__ import annotations

from typing import Any

from nvke._operator_install_plan import decimal, integer
from nvke._operator_install_review import SHA
from nvke.operator_contract import identifier


def request(body: Any, *, job_id: str, read_only: bool) -> dict[str, Any]:
    required = {"format", "artifactId", "planId", "planDigest"}
    optional = set()
    if not read_only:
        required |= {"maximumBytes", "maximumExpandedBytes"}
        optional = {"timeoutSeconds", "allowPlainHttp"}
    if not isinstance(body, dict) or not required <= body.keys() or body.keys() - (required | optional):
        raise ValueError
    integer(body["format"], 1, 1)
    identifier(job_id)
    identifier(body["artifactId"])
    identifier(body["planId"])
    if (
        body["artifactId"] in {job_id, body["planId"]}
        or body["planId"] == job_id
        or not isinstance(body["planDigest"], str)
        or not SHA.fullmatch(body["planDigest"])
    ):
        raise ValueError
    if not read_only:
        for field in ("maximumBytes", "maximumExpandedBytes"):
            if decimal(body[field]) == "0":
                raise ValueError
        integer(body.get("timeoutSeconds", 300), 1, 3600)
        if type(body.get("allowPlainHttp", False)) is not bool:
            raise ValueError
    return dict(body)


def capability(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "prepare", "readOnly", "continuation"}
        or type(value["format"]) is not int
        or value["format"] != 1
        or value["prepare"] is not True
        or value["readOnly"] is not True
        or value["continuation"] is not False
    ):
        raise ValueError


def summary(data: Any, *, body: dict[str, Any], item_count: int) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {
        "format",
        "artifactId",
        "planId",
        "planDigest",
        "artifactDigest",
        "chartCount",
        "bytes",
        "expandedBytes",
        "executionState",
        "continuationAuthorized",
    }:
        raise ValueError
    integer(data["format"], 1, 1)
    count = integer(data["chartCount"], 0, item_count)
    if (
        any(data[key] != body[key] for key in ("artifactId", "planId", "planDigest"))
        or not isinstance(data["artifactDigest"], str)
        or not SHA.fullmatch(data["artifactDigest"])
        or data["executionState"] != "requires-rendering-images-and-live-verification"
        or data["continuationAuthorized"] is not False
    ):
        raise ValueError
    for field, budget in (("bytes", "maximumBytes"), ("expandedBytes", "maximumExpandedBytes")):
        size = int(decimal(data[field]))
        if (count == 0) != (size == 0) or (budget in body and size > int(body[budget])):
            raise ValueError
    return dict(data)
