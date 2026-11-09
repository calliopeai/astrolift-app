"""Validate bounded public plan metadata; never expose private preparation payloads."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from nvke._operator_install_review import NAME, SHA, timestamp
from nvke.operator_contract import REVISION, identifier

DIGEST_FIELDS = {
    "bundleManifestSha256",
    "nativeArgumentsSha256",
    "retainedInputsSha256",
    "proposedInputsSha256",
    "proposedExecutionSha256",
    "privateSourceSha256",
    "reviewDigest",
    "stderrSha256",
}
SOURCE_FIELDS = DIGEST_FIELDS | {
    "siteId",
    "instanceId",
    "jobId",
    "nativeRunId",
    "nativeRevision",
    "nativeExitCode",
    "originalStartedAt",
    "reviewObservedAt",
    "originalDeploymentEvidence",
}
ITEM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
COUNTERS = ("generation", "observedGeneration", "desiredReplicas", "readyReplicas", "updatedReplicas")


def integer(value: Any, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError
    return value


def decimal(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value) or int(value) >= 2**63:
        raise ValueError
    return value


def summary(
    data: dict[str, Any], *, site: str, instance: str, bundle: str, job: str, plan_id: str, exit_code: int
) -> dict[str, Any]:
    if set(data) != {"format", "plan", "infrastructure", "itemCount", "retainedBytes", "proposedBytes"}:
        raise ValueError
    integer(data["format"], 1, 1)
    count = integer(data["itemCount"], 1, 10000)
    decimal(data["retainedBytes"])
    decimal(data["proposedBytes"])
    plan = data["plan"]
    if not isinstance(plan, dict) or set(plan) != {
        "format",
        "scope",
        "id",
        "preparedAt",
        "source",
        "phaseEvidence",
        "requiresOriginalSourceVerification",
        "requiresExplicitAcknowledgement",
        "continuationAuthorized",
        "firstRemainingOrdinal",
        "planDigest",
    }:
        raise ValueError
    integer(plan["format"], 1, 1)
    integer(plan["firstRemainingOrdinal"], 0, count - 1)
    if (
        plan["id"] != plan_id
        or plan["scope"] != "remaining-management-install-proposal"
        or plan["phaseEvidence"] != "retained-native-stderr-order-only"
        or plan["requiresOriginalSourceVerification"] is not True
        or plan["requiresExplicitAcknowledgement"] is not True
        or plan["continuationAuthorized"] is not False
        or not isinstance(plan["planDigest"], str)
        or not SHA.fullmatch(plan["planDigest"])
    ):
        raise ValueError
    source = plan["source"]
    if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
        raise ValueError
    identifier(source["nativeRunId"])
    if (
        source["siteId"] != site
        or source["instanceId"] != instance
        or source["jobId"] != job
        or source["bundleManifestSha256"] != bundle
        or source["nativeRevision"] != REVISION
        or integer(source["nativeExitCode"], -(2**31), 2**31 - 1) != exit_code
        or exit_code == 0
        or source["originalDeploymentEvidence"]
        not in {"retained-prepared-deployment", "reconstructed-prepared-deployment"}
        or any(not isinstance(source[k], str) or not SHA.fullmatch(source[k]) for k in DIGEST_FIELDS)
    ):
        raise ValueError
    started, observed, prepared = (
        timestamp(v) for v in (source["originalStartedAt"], source["reviewObservedAt"], plan["preparedAt"])
    )
    if not datetime.fromisoformat(started) <= datetime.fromisoformat(observed) <= datetime.fromisoformat(prepared):
        raise ValueError
    infrastructure = data["infrastructure"]
    if (
        not isinstance(infrastructure, dict)
        or set(infrastructure) != {"apiReady", "observedAt", "clusterUid", "workloadCount"}
        or infrastructure["apiReady"] is not True
        or timestamp(infrastructure["observedAt"]) != observed
    ):
        raise ValueError
    identifier(infrastructure["clusterUid"])
    integer(infrastructure["workloadCount"], 0, 10000)
    return {**data, "plan": {**plan, "source": dict(source)}, "infrastructure": dict(infrastructure)}


def workload(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != {"namespace", "kind", "name", "observation", *COUNTERS}:
        raise ValueError
    if (
        item["kind"] not in {"Deployment", "StatefulSet"}
        or any(not isinstance(item[k], str) or not NAME.fullmatch(item[k]) for k in ("namespace", "name"))
        or item["observation"] not in {"ready", "requires-review"}
    ):
        raise ValueError
    for key in COUNTERS:
        if item[key] is not None:
            integer(item[key], 0, 2**63 - 1)
    generation, observed, desired, ready, updated = (item[k] for k in COUNTERS)
    if (
        generation is None
        or desired is None
        or (
            item["observation"] == "ready"
            and (observed is None or observed < generation or desired == 0 or ready != desired or updated != desired)
        )
    ):
        raise ValueError
    return {**item, **{k: None if item[k] is None else str(item[k]) for k in COUNTERS}}


def page(
    data: dict[str, Any], *, metadata: dict[str, Any], view: str, limit: int, search: str, initial: bool = True
) -> dict[str, Any]:
    if set(data) != {"format", "view", "planId", "planDigest", "originalJobId", "items", "nextCursor", "totalCount"}:
        raise ValueError
    integer(data["format"], 1, 1)
    plan = metadata["plan"]
    maximum = metadata["itemCount"] if view == "items" else metadata["infrastructure"]["workloadCount"]
    total = integer(data["totalCount"], 0, maximum)
    if (
        data["view"] != view
        or data["planId"] != plan["id"]
        or data["planDigest"] != plan["planDigest"]
        or data["originalJobId"] != plan["source"]["jobId"]
        or (not search and total != maximum)
        or not isinstance(data["items"], list)
        or len(data["items"]) > min(limit, total)
    ):
        raise ValueError
    if initial and len(data["items"]) != min(limit, total):
        raise ValueError
    cursor = data["nextCursor"]
    if cursor is not None and (
        not isinstance(cursor, str) or not 1 <= len(cursor) <= 8192 or len(data["items"]) != limit or total <= limit
    ):
        raise ValueError
    if initial and (cursor is not None) != (total > limit):
        raise ValueError
    if total and not data["items"]:
        raise ValueError
    projected, seen = [], set()
    previous = -1
    identity: str | tuple[Any, Any, Any]
    for item in data["items"]:
        if view == "workloads":
            row = workload(item)
            identity = (row["namespace"], row["kind"], row["name"])
        else:
            if not isinstance(item, dict) or set(item) != {
                "name",
                "type",
                "contentSha256",
                "originalType",
                "originalContentSha256",
                "ordinal",
                "originalObservation",
                "proposedAction",
            }:
                raise ValueError
            ordinal = integer(item["ordinal"], 0, maximum - 1)
            first = plan["firstRemainingOrdinal"]
            observation = (
                "returned-before-next-observed-item"
                if ordinal < first
                else "last-observed-in-unsuccessful-run"
                if ordinal == first
                else "not-observed"
            )
            if (
                ordinal <= previous
                or not isinstance(item["name"], str)
                or not ITEM_NAME.fullmatch(item["name"])
                or item["type"] not in {"template", "manifest", "helm"}
                or item["originalType"] not in {"template", "manifest", "helm"}
                or any(
                    not isinstance(item[k], str) or not SHA.fullmatch(item[k])
                    for k in ("contentSha256", "originalContentSha256")
                )
                or item["originalObservation"] != observation
                or item["proposedAction"] != ("skip" if ordinal < first else "execute")
            ):
                raise ValueError
            previous = ordinal
            identity = item["name"]
            row = dict(item)
        if identity in seen:
            raise ValueError
        seen.add(identity)
        projected.append(row)
    return {**data, "items": projected}
