"""Extract the pinned controller credential gauges and agent inventory."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

SOURCES = (
    "controller/internal/dbmetrics/credentials.go",
    "agent/pkg/certificates/catalog.go",
    "controller/internal/dbmetrics/dbmetrics.go",
)


def inventory(checkout: Path) -> dict[str, Any]:
    collector, catalog, common = ((checkout / path).read_text() for path in SOURCES)
    definitions = collector.split("definitions :=", 1)[1].split("descriptors :=", 1)[0]
    constants = dict(re.findall(r'(\w+)\s*=\s*"([^"\n]+)"', collector + common))
    labels = {name: constants[name] for name in ("labelTenantCluster", "labelNode", "labelCredential", "labelState")}
    metrics = []
    for name, raw in re.findall(r'\{"([a-z_]+)", "[^"\n]+", (nil|\[\]string\{[^}]+\})\}', definitions):
        names = [] if raw == "nil" else raw.removeprefix("[]string{").removesuffix("}").split(",")
        metrics.append({"name": name, "labels": [labels[item.strip()] for item in names]})
    names_body = catalog.split("func Names(controlPlane bool)", 1)[1].split("// Supports", 1)[0]
    names = re.findall(r'"([a-z-]+)"', names_body)
    if len(metrics) != 18 or len({row["name"] for row in metrics}) != 18 or len(names) != 16 or len(set(names)) != 16:
        raise ValueError("credential collector/catalog changed; review the complete native inventory")
    if 'return name != "super-admin"' not in catalog:
        raise ValueError("credential applicability changed; review upstream")
    states = {}
    for raw, metric in re.findall(
        r'for _, candidate := range \[\]string\{([^}]+)\}\s*\{\s*c.emit\(ch, "([a-z_]+)"', collector
    ):
        states[metric] = [
            json.loads(item.strip()) if item.strip().startswith('"') else constants[item.strip()]
            for item in raw.split(",")
        ]
    csr = re.search(r"counts := map\[string\]int\{([^}]+)\}", collector)
    if csr is None:
        raise ValueError("credential CSR inventory changed; review upstream")
    states["serving_csr_count"] = [
        json.loads(item) if item.startswith('"') else constants[item]
        for item in re.findall(r'("[^"]+"|\w+):\s*0', csr[1])
    ]
    if set(states) != {"renewal_state", "etcd_key_rotation_state", "serving_csr_count"}:
        raise ValueError("credential lifecycle vocabulary changed; review upstream")
    prefix = re.search(r'prometheus.NewDesc\("([a-z_]+)"\+d.name', collector)
    if prefix is None:
        raise ValueError("credential metric prefix changed; review upstream")
    return {
        "prefix": prefix[1],
        "format": "prometheus-text-0.0.4",
        "source": SOURCES[0],
        "credential_source": SOURCES[1],
        "metrics": sorted(metrics, key=lambda row: row["name"]),
        "states": states,
        "credentials": [{"name": name, "required": name != "super-admin"} for name in names],
    }
