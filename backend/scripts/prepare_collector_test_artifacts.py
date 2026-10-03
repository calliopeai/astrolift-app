"""Prepare checksum-pinned native collector artifacts for CI, without Docker."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import urllib.request
from pathlib import Path

from install_helm import MAX_ARCHIVE_BYTES, install_archive

ROOT = Path(__file__).resolve().parents[1]
PIN_ROOT = ROOT / "providers" / "aws" / "_cloudwatch_collector"


def download(url: str, limit: int) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(limit + 1)
    if not data or len(data) > limit:
        raise RuntimeError("Collector test artifact exceeds its bounded size")
    return data


def prepare(directory: Path, target: str) -> None:
    helm = json.loads((PIN_ROOT / "helm-runtime.json").read_text())
    chart = json.loads((PIN_ROOT / "upstream-chart.json").read_text())
    if target not in helm["archives"]:
        raise RuntimeError("Collector test runtime target has no reviewed checksum")
    directory.mkdir(parents=True, exist_ok=True)
    archive = download(f"https://get.helm.sh/helm-{helm['version']}-{target}.tar.gz", MAX_ARCHIVE_BYTES)
    install_archive(
        archive,
        target=target,
        digest=helm["archives"][target],
        destination=directory / f"helm-{helm['version']}-{target}",
    )
    name = f"{chart['name']}-{chart['version']}"
    archive = download(
        f"https://github.com/fluent/helm-charts/releases/download/{name}/{name}.tgz", 10_000_000
    )
    if hashlib.sha256(archive).hexdigest() != chart["archiveSha256"]:
        raise RuntimeError("Collector test chart differs from its reviewed checksum")
    (directory / f"{name}.tgz").write_bytes(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--target",
        default=f"{platform.system().lower()}-"
        + {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine(), "unsupported"),
    )
    args = parser.parse_args()
    prepare(args.directory, args.target)
