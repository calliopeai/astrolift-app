"""Prepare the checksum-pinned Prometheus query engine for native CI tests."""

from __future__ import annotations

import argparse
import hashlib
import io
import platform
import tarfile
import urllib.request
from pathlib import Path

VERSION = "2.55.0"
# https://github.com/prometheus/prometheus/releases/download/v2.55.0/sha256sums.txt
ARCHIVES = {
    "darwin-arm64": "1bcbd3c4aa232dcd2f1b93525da19f18fbaf68225f2cc73a34569734aef30a14",
    "linux-amd64": "7a6b6d5ea003e8d59def294392c64e28338da627bf760cf268e788d6a8832a23",
    "linux-arm64": "29965342c79cccba08d28ea530baa38d21d9e6a02e78a565cd59656df6f406c7",
}
MAX_ARCHIVE_BYTES = 200_000_000
MAX_BINARY_BYTES = 300_000_000


def install(archive: bytes, *, directory: Path, target: str) -> Path:
    if not 0 < len(archive) <= MAX_ARCHIVE_BYTES:
        raise RuntimeError("Prometheus test archive exceeds its bounded size")
    if target not in ARCHIVES or hashlib.sha256(archive).hexdigest() != ARCHIVES[target]:
        raise RuntimeError("Prometheus test archive differs from its reviewed checksum")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        member = bundle.getmember(f"prometheus-{VERSION}.{target}/promtool")
        if not member.isfile() or not 0 < member.size <= MAX_BINARY_BYTES:
            raise RuntimeError("Prometheus test engine is not a bounded regular file")
        source = bundle.extractfile(member)
        if source is None:
            raise RuntimeError("Prometheus test engine is absent")
        body = source.read(MAX_BINARY_BYTES + 1)
    if len(body) != member.size:
        raise RuntimeError("Prometheus test engine is incomplete")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "promtool"
    destination.write_bytes(body)
    destination.chmod(0o755)
    return destination


def prepare(directory: Path, target: str) -> Path:
    if target not in ARCHIVES:
        raise RuntimeError("Prometheus test target has no reviewed checksum")
    name = f"prometheus-{VERSION}.{target}.tar.gz"
    url = f"https://github.com/prometheus/prometheus/releases/download/v{VERSION}/{name}"
    with urllib.request.urlopen(url, timeout=30) as response:
        archive = response.read(MAX_ARCHIVE_BYTES + 1)
    if not 0 < len(archive) <= MAX_ARCHIVE_BYTES:
        raise RuntimeError("Prometheus test archive exceeds its bounded size")
    return install(archive, directory=directory, target=target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--target",
        default=f"{platform.system().lower()}-"
        + {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine(), "unsupported"),
    )
    args = parser.parse_args()
    print(prepare(args.directory, args.target))
