"""Install the pinned, AWS-signed confidential image tooling during the backend build."""

from __future__ import annotations

import hashlib
import io
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "0.1.4274.0"
MANIFESTS = {
    "x86_64": "33df467673f08654c611c0820bd7c6449a7bd5280c3a0690e36dbb475ceed972",
    "aarch64": "5619221756c14cf90444bbabe9fa6546ac6b849f585dbbcbb59ebcf207cf7920",
}
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MHYwEAYHKoZIzj0CAQYFK4EEACIDYgAESqdVXO82n+Zh5+IFLh1xjmxZLkjjZWvf
DCS927SdVYjP0AZ92XCxS+ucrs9cjuNlTaDARTHov2XVfMVgau8TEPFKGDRmVZXJ
c25CVihKHLFK8T8hNMIL+fbGcvE2fwIO
-----END PUBLIC KEY-----
"""
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def unpack_verified(
    archive: bytes, manifest: bytes, signature: bytes, *, architecture: str, destination: Path
) -> None:
    if architecture not in MANIFESTS or hashlib.sha256(manifest).hexdigest() != MANIFESTS[architecture]:
        raise RuntimeError("AWS runtime checksum manifest differs from the reviewed release")
    with tempfile.TemporaryDirectory(prefix="verify-nitro-runtime-") as temporary:
        root = Path(temporary)
        (root / "public.pem").write_bytes(PUBLIC_KEY)
        (root / "SHA256SUM").write_bytes(manifest)
        (root / "SHA256SUM.sig").write_bytes(signature)
        result = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha384",
                "-verify",
                str(root / "public.pem"),
                "-signature",
                str(root / "SHA256SUM.sig"),
                str(root / "SHA256SUM"),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError("AWS runtime signature verification failed")
    expected = {line.split()[1].removeprefix("*"): line.split()[0] for line in manifest.decode().splitlines()}
    if (
        not archive
        or len(archive) > MAX_ARCHIVE_BYTES
        or hashlib.sha256(archive).hexdigest() != expected["nitro-sandboxes-runtime.tar.gz"]
    ):
        raise RuntimeError("AWS runtime archive differs from its signed checksum")
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            if not member.isfile() or member.name != Path(member.name).name or member.name in contents:
                raise RuntimeError("AWS runtime archive must contain flat unique regular files")
            stream = bundle.extractfile(member)
            if stream is None or member.size > MAX_ARCHIVE_BYTES:
                raise RuntimeError("AWS runtime archive entry is unavailable or oversized")
            contents[member.name] = stream.read(MAX_ARCHIVE_BYTES + 1)
    for name, checksum in expected.items():
        if (
            name != "nitro-sandboxes-runtime.tar.gz"
            and hashlib.sha256(contents[name]).hexdigest() != checksum
        ):
            raise RuntimeError("AWS runtime binary differs from its signed checksum")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("eks-nitro-sandbox-genpolicy", "nitro-sandbox-keyprovider", "LICENSE", "ATTRIBUTION"):
        (destination / name).write_bytes(contents[name])
        (destination / name).chmod(
            0o755 if name in {"eks-nitro-sandbox-genpolicy", "nitro-sandbox-keyprovider"} else 0o644
        )
    (destination / "SHA256SUM").write_bytes(manifest)
    (destination / "SHA256SUM.sig").write_bytes(signature)


def main() -> None:
    architecture = platform.machine()
    if platform.system() != "Linux" or architecture not in MANIFESTS:
        raise RuntimeError("AWS confidential runtime tools require Linux x86_64 or aarch64")
    base = f"https://d1uvmbvklg8rt.cloudfront.net/{VERSION}/{architecture}/"
    payload = {}
    for name in ("SHA256SUM", "SHA256SUM.sig", "nitro-sandboxes-runtime.tar.gz"):
        with urllib.request.urlopen(base + name, timeout=60) as response:
            payload[name] = response.read(MAX_ARCHIVE_BYTES + 1)
    unpack_verified(
        payload["nitro-sandboxes-runtime.tar.gz"],
        payload["SHA256SUM"],
        payload["SHA256SUM.sig"],
        architecture=architecture,
        destination=Path("/opt/astrolift/nitro"),
    )


if __name__ == "__main__":
    main()
