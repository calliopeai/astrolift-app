"""Project the agent's partial-install review without exposing arbitrary diagnostics."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from nvke.operator_contract import REVISION, canonical, identifier

SHA = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}\Z")


def timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.isoformat()


def project(
    data: dict[str, Any], *, site: str, instance: str, bundle: str, job: str, intent: str, exit_code: int
) -> dict[str, Any]:
    if set(data) != {"source", "review", "reviewDigest"}:
        raise ValueError
    source, report = data["source"], data["review"]
    run_id = identifier(source["nativeRunId"])
    if (
        source
        != {
            "format": 1,
            "revision": REVISION,
            "siteId": site,
            "instanceId": instance,
            "bundleManifestSha256": bundle,
            "id": job,
            "intent": intent,
            "command": "management.install",
            "nativeRunId": run_id,
        }
        or type(source["format"]) is not int
    ):
        raise ValueError
    if (
        not isinstance(report, dict)
        or set(report)
        != {
            "format",
            "observedAt",
            "scope",
            "nativeRevision",
            "reviewerSha256",
            "qualificationHelperSha256",
            "bundle",
            "originalAttempt",
            "privateEvidenceSha256",
            "apiReady",
            "workloads",
            "continuationAuthorized",
            "limitations",
        }
        or type(report["format"]) is not int
        or report["format"] != 1
        or report["scope"] != "read-only-retained-partial-management-install-review"
        or report["nativeRevision"] != REVISION
        or report["apiReady"] is not True
        or report["continuationAuthorized"] is not False
        or data["reviewDigest"] != hashlib.sha256(canonical({"source": source, "review": report})).hexdigest()
    ):
        raise ValueError
    for key in ("reviewerSha256", "qualificationHelperSha256"):
        if not isinstance(report[key], str) or not SHA.fullmatch(report[key]):
            raise ValueError
    manifest = report["bundle"]
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"format", "revision", "binarySha256", "sourceArchiveSha256", "sourceTreeSha256", "capiVersion"}
        or type(manifest["format"]) is not int
        or manifest["format"] != 1
        or manifest["revision"] != REVISION
        or not isinstance(manifest["capiVersion"], str)
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", manifest["capiVersion"])
        or any(
            not isinstance(manifest[k], str) or not SHA.fullmatch(manifest[k])
            for k in ("binarySha256", "sourceArchiveSha256", "sourceTreeSha256")
        )
    ):
        raise ValueError
    attempt = report["originalAttempt"]
    if (
        not isinstance(attempt, dict)
        or set(attempt) != {"runId", "startedAt", "argumentsDigest", "phase", "exitCode"}
        or attempt["runId"] != run_id
        or attempt["phase"] != "uncertain"
        or type(attempt["exitCode"]) is not int
        or attempt["exitCode"] != exit_code
        or exit_code == 0
        or not isinstance(attempt["argumentsDigest"], str)
        or not SHA.fullmatch(attempt["argumentsDigest"])
    ):
        raise ValueError
    hashes = report["privateEvidenceSha256"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != {"guard", "started", "finished", "stdout", "stderr"}
        or any(not isinstance(v, str) or not SHA.fullmatch(v) for v in hashes.values())
    ):
        raise ValueError
    items = report["workloads"]
    if not isinstance(items, list) or not 1 <= len(items) <= 10000:
        raise ValueError
    workloads, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "namespace",
            "kind",
            "name",
            "generation",
            "observedGeneration",
            "desiredReplicas",
            "readyReplicas",
            "updatedReplicas",
            "observation",
        }:
            raise ValueError
        if (
            item["kind"] not in {"Deployment", "StatefulSet"}
            or any(not isinstance(item[k], str) or not NAME.fullmatch(item[k]) for k in ("namespace", "name"))
            or item["observation"] not in {"ready", "requires-review"}
        ):
            raise ValueError
        workload_identity = (item["namespace"], item["kind"], item["name"])
        if workload_identity in seen:
            raise ValueError
        seen.add(workload_identity)
        counters = [
            item[k] for k in ("generation", "observedGeneration", "desiredReplicas", "readyReplicas", "updatedReplicas")
        ]
        if any(v is not None and (type(v) is not int or not 0 <= v < 2**63) for v in counters):
            raise ValueError
        generation, observed, desired, ready, updated = counters
        if (
            generation is None
            or desired is None
            or (
                item["observation"] == "ready"
                and (
                    observed is None or observed < generation or desired == 0 or ready != desired or updated != desired
                )
            )
        ):
            raise ValueError
        workloads.append(dict(item))
    # Arbitrary limitations/diagnostic text and the raw manifest never cross this boundary.
    return {
        "source": dict(source),
        "reviewDigest": data["reviewDigest"],
        "observedAt": timestamp(report["observedAt"]),
        "originalAttempt": {**attempt, "startedAt": timestamp(attempt["startedAt"])},
        "privateEvidenceSha256": dict(hashes),
        "reviewerSha256": report["reviewerSha256"],
        "qualificationHelperSha256": report["qualificationHelperSha256"],
        "apiReady": True,
        "workloads": workloads,
        "continuationAuthorized": False,
    }
