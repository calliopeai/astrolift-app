"""Pin the NVKE interfaces used by Astro; never fetch upstream during tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "nvke" / "upstream" / "contract.json"
METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def inventory(checkout: Path) -> dict[str, Any]:
    revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"], text=True
    )
    if dirty:
        raise ValueError("upstream tracked files must be clean before pinning")
    spec_path = "controller/api/openapi.yaml"
    openapi = yaml.safe_load((checkout / spec_path).read_text())
    operations = []
    for path, item in openapi["paths"].items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            operations.append(
                {"id": operation["operationId"], "surface": "controller", "method": method.upper(), "path": path}
            )

    registration_path = "management-engine/pkg/publicapi/zz_generated.registrations.go"
    registrations = (checkout / registration_path).read_text()
    surface_path = "management-engine/pkg/publicapi/surfaces.go"
    surfaces = (checkout / surface_path).read_text()
    route_path = "management-engine/pkg/resource/server.go"
    route_source = (checkout / route_path).read_text()
    routes = re.findall(r'\{Op(\w+), http.Method(\w+), (""|itemPathSuffix)\}', route_source)
    if len(routes) != 5:
        raise ValueError("unrecognized NVKE public resource route table; review upstream")
    for name, body in re.findall(r"func (Tenant|Operator)\(\).*?\{(.*?)\n\}", surfaces, re.S):
        for resource, verbs in re.findall(r"(\w+)\((resource.Op\w+[^)]*)\)", body):
            match = re.search(rf"func {resource}\(.*?\n\}}", registrations, re.S)
            if match is None:
                raise ValueError(f"missing registration for {resource}")
            base = re.search(r'\]\("([^"]+)"', match[0])
            if base is None:
                raise ValueError(f"missing route for {resource}")
            for verb, method, suffix in routes:
                if f"resource.Op{verb}" in verbs:
                    operations.append(
                        {
                            "id": f"{name.lower()}.{verb.lower()}{resource}",
                            "surface": name.lower(),
                            "method": method.upper(),
                            "path": base[1] + ("/{name}" if suffix == "itemPathSuffix" else ""),
                            "resource": resource,
                        }
                    )
        for endpoint in ("/healthz", "/readyz"):
            operations.append(
                {"id": f"{name.lower()}.{endpoint[1:]}", "surface": name.lower(), "method": "GET", "path": endpoint}
            )

    crds = []
    for path in sorted((checkout / "management-engine/config/crd/bases").glob("*.yaml")):
        crd = yaml.safe_load(path.read_text())
        if crd.get("kind") != "CustomResourceDefinition":
            raise ValueError(f"unexpected file in NVKE CRD directory: {path.name}")
        crds.append(crd)
    if not crds:
        raise ValueError("no management-engine CRDs found")

    command_docs = {}
    for path in sorted((checkout / "cli/docs/reference").glob("nvkectl*.md")):
        content = path.read_text()
        match = re.search(r"^## (nvkectl[^\n]*)$", content, re.M)
        if match:
            command_docs[match[1]] = {"source": str(path.relative_to(checkout)), "reference": content}
    # The checked-in CLI reference can lag the executable source. Keep source
    # declarations and hashes as a second inventory rather than trust the docs.
    declarations = []
    for path in sorted((checkout / "cli/internal").rglob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        content = path.read_text()
        for use in re.findall(r'\bUse:\s*"([^"\n]+)"', content):
            declarations.append({"use": use, "source": str(path.relative_to(checkout))})

    sources = {
        spec_path,
        registration_path,
        surface_path,
        route_path,
        "management-engine/pkg/publicapi/server.go",
        "LICENSE",
        "NOTICE",
    }
    for pattern in (
        "management-engine/apis/v1/*.go",
        "management-engine/pkg/resource/*.go",
        "management-engine/config/crd/bases/*.yaml",
        "cli/docs/reference/*.md",
        "cli/internal/**/*.go",
        "docs/operate/*.md",
        "docs/site-bootstrap/*.md",
        "docs/site-operations/*.md",
        "docs/workload-clusters/*.md",
        "docs/tenant-clusters/*.md",
        "docs/overview/*.md",
        "docs/nktp/**/*.md",
    ):
        sources.update(str(p.relative_to(checkout)) for p in checkout.glob(pattern) if not p.name.endswith("_test.go"))
    return {
        "repository": "https://github.com/dsx-ai-factory/nvke",
        "revision": revision,
        "license": "Apache-2.0",
        "sources": {s: hashlib.sha256((checkout / s).read_bytes()).hexdigest() for s in sorted(sources)},
        "operations": sorted(operations, key=lambda op: op["id"]),
        "openapi": openapi,
        "custom_resources": crds,
        "cli_reference": command_docs,
        "cli_source_declarations": declarations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = _json(inventory(args.upstream.resolve()))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            raise SystemExit("NVKE upstream contract drifted; review every changed interface before regenerating")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(content)
        for name in ("LICENSE", "NOTICE"):
            (OUTPUT.parent / name).write_bytes((args.upstream / name).read_bytes())
    print(f"NVKE contract {'verified' if args.check else 'pinned'}: {OUTPUT}")


if __name__ == "__main__":
    main()
