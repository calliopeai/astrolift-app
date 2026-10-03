"""Install only the reviewed Helm binary in the backend build, without a shell."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import sys
import tarfile
import urllib.request
from pathlib import Path

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024


def install_archive(archive: bytes, *, target: str, digest: str, destination: Path) -> None:
    if not archive or len(archive) > MAX_ARCHIVE_BYTES or hashlib.sha256(archive).hexdigest() != digest:
        raise RuntimeError("Helm archive does not match the reviewed checksum")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            entries = [entry for entry in bundle.getmembers() if entry.name == f"{target}/helm"]
            if len(entries) != 1 or not entries[0].isfile() or not 0 < entries[0].size <= MAX_BINARY_BYTES:
                raise RuntimeError("Helm archive has no unique bounded regular executable")
            stream = bundle.extractfile(entries[0])
            if stream is None:
                raise RuntimeError("Helm archive executable is unavailable")
            data = stream.read(MAX_BINARY_BYTES + 1)
        if len(data) != entries[0].size:
            raise RuntimeError("Helm archive executable is truncated")
        destination.write_bytes(data)
        destination.chmod(0o755)
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Helm archive installation failed") from None


def main() -> None:
    pin = json.loads(Path(sys.argv[1]).read_text())
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if platform.system() != "Linux" or architecture is None:
        raise RuntimeError("Backend Helm renderer requires a supported Linux architecture")
    target = f"linux-{architecture}"
    url = f"https://get.helm.sh/helm-{pin['version']}-{target}.tar.gz"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
    except Exception:
        raise RuntimeError("Helm archive download failed") from None
    install_archive(
        archive, target=target, digest=pin["archives"][target], destination=Path("/usr/local/bin/helm")
    )


if __name__ == "__main__":
    main()
