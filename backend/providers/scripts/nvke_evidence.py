"""Validate delivery evidence independently from the pinned upstream inventory."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from nvke.contract import _contract, resources, upstream_revision
from nvke.operations import actions

ROOT = Path(__file__).resolve().parents[3]
LEDGER = Path(__file__).with_name("nvke_evidence.json")
OPERATOR_FAMILIES = {
    "site-configuration": (
        "Site configuration",
        "Register/select/edit/probe/remove sites, secret references, supported interface discovery",
    ),
    "node-preparation": (
        "Node preparation",
        "Images, registry/boot artifacts, SPIRE CA/TPM or admitted alternative, pre-provisioned nodes",
    ),
    "site-specs": (
        "Site specs",
        "Generate, inspect, validate and dry-run the complete spec; report field errors without secrets",
    ),
    "deployment-rendering": (
        "Deployment rendering",
        "Inspect Helm values and stack config; render pinned deploy path and release inputs",
    ),
    "bootstrap": (
        "Bootstrap",
        "Ephemeral seed, pre-provisioned/NICo path, timeout/progress/diagnostics, "
        "kubeconfig and encryption-key outputs",
    ),
    "installation": (
        "Installation",
        "Ordered CAPI/provider/management-plane install; validate pinned images, versions, storage and network",
    ),
    "infrastructure-providers": (
        "Infrastructure providers",
        "NICo and enabled LaunchLayer; CRDs, credentials and manager state; credential-health freshness",
    ),
    "artifacts": (
        "Artifacts",
        "Machine images, cluster releases, node-pool releases, release channels and control-plane classes",
    ),
    "access": (
        "Access",
        "Kubeconfig retrieval and OIDC config generation, narrow automation identity, private route validation",
    ),
    "credentials": (
        "Credentials",
        "Certificate expiry/renewal, SPIRE settings, bootstrap outputs and secure output/download",
    ),
    "maintenance": (
        "Maintenance",
        "Control-plane and worker version changes, node replacement, drain/rollout conditions and partial failure",
    ),
    "networking": (
        "Networking",
        "CNI, VIP and API access/CIDRs; separate immutable creation fields from day-2 updates",
    ),
    "telemetry": (
        "Telemetry",
        "NKTP/OTLP deployment and collection; health/events/logs/metrics, config and connectivity diagnostics",
    ),
    "unsupported-upstream": (
        "Unsupported upstream",
        "No claimed self-management, management-plane upgrade or backup/restore without a verified implementation",
    ),
}


def expected_rows() -> dict[str, set[str]]:
    contract = _contract()
    return {
        "actions": {a.key for a in actions()},
        "resources": {r.kind for r in resources()},
        "workflows": {w["name"] for w in contract["native_workflows"]},
        "metrics": {m["name"] for m in contract["credential_metrics"]["metrics"]},
        "commands": {c["path"] for c in contract["cli_runtime"] if c["executable"]},
        "operators": set(OPERATOR_FAMILIES),
    }


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value and "\r" not in value


def validate(data: dict[str, Any], related: dict[str, Path] | None = None) -> dict[str, Any]:
    if (
        set(data) != {"format", "upstreamRevision", "repositories", "evidence", "rows"}
        or data["format"] != 1
        or data["upstreamRevision"] != upstream_revision()
    ):
        raise ValueError("NVKE evidence schema or upstream pin differs")
    repositories, evidence, rows = data["repositories"], data["evidence"], data["rows"]
    if set(repositories) != {"app", "cli", "opscode"}:
        raise ValueError("NVKE evidence repositories differ")
    for key, repo in repositories.items():
        repository_fields = {"name"} if key == "app" else {"name", "revision"}
        if set(repo) != repository_fields or repo["name"] != "calliopeai/astrolift-" + key:
            raise ValueError("NVKE evidence repository identity differs")
        if key != "app" and not re.fullmatch(r"[0-9a-f]{40}", repo["revision"]):
            raise ValueError("NVKE paired evidence needs an immutable commit")
    for identifier, item in evidence.items():
        if not re.fullmatch(r"[a-z][a-z0-9-]*", identifier) or item.get("repository") not in repositories:
            raise ValueError("NVKE evidence reference identity differs")
        repo = item["repository"]
        required = {"repository", "path", "kind", "scope"} | ({"sha256"} if repo != "app" else set())
        if not required <= item.keys() or item.keys() - required - {"symbol"}:
            raise ValueError("NVKE evidence reference fields differ")
        path = PurePosixPath(item["path"])
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or "\\" in item["path"]
            or not _text(item["scope"])
            or item["kind"] not in {"implementation", "test", "observation"}
        ):
            raise ValueError("NVKE evidence path/scope is invalid")
        content = None
        if repo == "app":
            local = ROOT / path
            if not local.is_file() or not local.resolve().is_relative_to(ROOT):
                raise ValueError("NVKE application evidence source is missing")
            content = local.read_bytes()
        else:
            if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise ValueError("NVKE paired evidence needs a source digest")
            if related and repo in related:
                content = subprocess.check_output(
                    ["git", "-C", str(related[repo]), "show", f"{repositories[repo]['revision']}:{path}"],
                    stderr=subprocess.DEVNULL,
                )
                if hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ValueError("NVKE paired evidence differs from its immutable source")
        if "symbol" in item and (
            not _text(item["symbol"]) or (content is not None and item["symbol"].encode() not in content)
        ):
            raise ValueError("NVKE evidence test selector is missing")
    expected = expected_rows()
    if set(rows) != set(expected) or any(set(rows[k]) != keys for k, keys in expected.items()):
        raise ValueError("NVKE evidence must cover every row exactly once; review missing or unknown rows")
    referenced = set()
    for family_name, family in rows.items():
        for row_key, row in family.items():
            if (
                set(row) != {"mapping", "qualification", "evidence", "remaining"}
                or not _text(row["mapping"])
                or not _text(row["remaining"])
            ):
                raise ValueError("NVKE evidence row needs an explicit mapping and remaining qualification")
            if row["qualification"] not in {"fixture", "local", "partial", "missing", "unsupported"}:
                raise ValueError("NVKE evidence cannot infer complete/live support")
            if row["qualification"] == "unsupported" and (family_name, row_key) != (
                "operators",
                "unsupported-upstream",
            ):
                raise ValueError("NVKE exposed native surface cannot be reclassified as unsupported")
            refs = row["evidence"]
            if (
                not isinstance(refs, list)
                or not all(isinstance(r, str) and r in evidence for r in refs)
                or len(refs) != len(set(refs))
            ):
                raise ValueError("NVKE evidence row references missing or repeated sources")
            if row["qualification"] != "missing" and not refs:
                raise ValueError("NVKE non-missing qualification needs executable or documented evidence")
            if row["qualification"] in {"fixture", "local"} and not any(
                evidence[ref]["kind"] in {"test", "observation"} for ref in refs
            ):
                raise ValueError("NVKE execution qualification needs a test or observation source")
            referenced.update(refs)
    if referenced != set(evidence):
        raise ValueError("NVKE unreferenced evidence must not imply coverage")
    return data


def load(path: Path = LEDGER, related: dict[str, Path] | None = None) -> dict[str, Any]:
    return validate(json.loads(path.read_text()), related)


def cell(data: dict[str, Any], family: str, key: str) -> str:
    row = data["rows"][family][key]
    refs = ", ".join(f"[{ref}](#evidence-{ref})" for ref in row["evidence"]) or "No delivery evidence"
    return f"**{row['qualification']}** — {refs}. Remaining: {row['remaining']}".replace("|", "\\|")


def sources(data: dict[str, Any]) -> list[str]:
    lines = [
        "## Evidence sources and limits",
        "",
        "These references describe source/test scope and recorded observations, not a current green test run.",
        "Paired repository links name local immutable commits and become remotely readable "
        "after those commits are pushed.",
        "All linked suites and required build/lint/type gates must run again as appropriate before the final PR.",
        "",
    ]
    for identifier, item in data["evidence"].items():
        repo = data["repositories"][item["repository"]]
        url = (
            f"../../{item['path']}"
            if item["repository"] == "app"
            else f"https://github.com/{repo['name']}/blob/{repo['revision']}/{item['path']}"
        )
        lines.extend(
            [f"### Evidence {identifier}", "", f"[{item['path']}]({url}) — {item['kind']}. {item['scope']}", ""]
        )
        if "symbol" in item:
            lines.extend([f"Selector: `{item['symbol']}`.", ""])
        if "sha256" in item:
            lines.extend([f"Paired source SHA-256: `{item['sha256']}`.", ""])
    return lines
