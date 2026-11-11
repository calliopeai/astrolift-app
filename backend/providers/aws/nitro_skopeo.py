"""Verify the installed image tool with an offline, public-only keyprovider round trip."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

MANIFEST = "application/vnd.oci.image.manifest.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
PROVIDER = """import json, sys
from pathlib import Path
request = json.load(sys.stdin)
operation = request['op']
with Path(sys.argv[1]).open('a') as stream:
    stream.write(operation + '\\n')
if operation == 'keywrap':
    result = {'keywrapresults': {'annotation': request['keywrapparams']['optsdata']}}
elif operation == 'keyunwrap':
    result = {'keyunwrapresults': {'optsdata': request['keyunwrapparams']['annotation']}}
else:
    raise SystemExit(1)
json.dump(result, sys.stdout)
"""


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _blob(layout: Path, data: bytes, media_type: str) -> dict[str, object]:
    digest = hashlib.sha256(data).hexdigest()
    (layout / "blobs" / "sha256" / digest).write_bytes(data)
    return {"mediaType": media_type, "digest": "sha256:" + digest, "size": len(data)}


def _manifest(layout: Path) -> dict:
    descriptor = json.loads((layout / "index.json").read_bytes())["manifests"][0]
    return json.loads((layout / "blobs" / "sha256" / descriptor["digest"].split(":")[1]).read_bytes())


def verify_keyprovider(skopeo: str, temporary_root: str | None = None) -> None:
    # This reversible provider is confined to a generated public fixture, never a run image or registry.
    with tempfile.TemporaryDirectory(prefix="nitro-skopeo-check-", dir=temporary_root) as directory:
        root = Path(directory)
        source = root / "source"
        (source / "blobs" / "sha256").mkdir(parents=True)
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode="w") as archive:
            data = b"Astrolift public keyprovider capability probe\n"
            entry = tarfile.TarInfo("public-probe.txt")
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        plaintext = gzip.compress(layer.getvalue(), mtime=0)
        config = _blob(
            source,
            _json(
                {
                    "architecture": "amd64",
                    "os": "linux",
                    "config": {},
                    "rootfs": {
                        "type": "layers",
                        "diff_ids": ["sha256:" + hashlib.sha256(layer.getvalue()).hexdigest()],
                    },
                }
            ),
            "application/vnd.oci.image.config.v1+json",
        )
        descriptor = _blob(
            source,
            _json(
                {
                    "schemaVersion": 2,
                    "mediaType": MANIFEST,
                    "config": config,
                    "layers": [_blob(source, plaintext, LAYER)],
                }
            ),
            MANIFEST,
        )
        descriptor["annotations"] = {"org.opencontainers.image.ref.name": "probe"}
        (source / "index.json").write_bytes(_json({"schemaVersion": 2, "manifests": [descriptor]}))
        (source / "oci-layout").write_bytes(_json({"imageLayoutVersion": "1.0.0"}))
        provider = root / "provider.py"
        provider.write_text(PROVIDER)
        operations = root / "operations"
        settings = root / "ocicrypt.json"
        settings.write_bytes(
            _json(
                {
                    "key-providers": {
                        "probe": {"cmd": {"path": sys.executable, "args": [str(provider), str(operations)]}}
                    }
                }
            )
        )
        policy = root / "policy.json"
        policy.write_bytes(_json({"default": [{"type": "insecureAcceptAnything"}]}))
        environment = {
            "PATH": os.defpath,
            "HOME": str(root),
            "TMPDIR": str(root),
            "TMP": str(root),
            "TEMP": str(root),
            "OCICRYPT_KEYPROVIDER_CONFIG": str(settings),
        }
        encrypted, decrypted = root / "encrypted", root / "decrypted"
        common = [skopeo, "--tmpdir", str(root), "--policy", str(policy), "copy"]
        for arguments in (
            [
                "--encryption-key",
                "provider:probe:public",
                "oci:" + str(source) + ":probe",
                "oci:" + str(encrypted) + ":probe",
            ],
            [
                "--decryption-key",
                "provider:probe",
                "oci:" + str(encrypted) + ":probe",
                "oci:" + str(decrypted) + ":probe",
            ],
        ):
            result = subprocess.run(common + arguments, env=environment, capture_output=True, timeout=30)
            if result.returncode:
                raise ValueError("Installed Skopeo failed the offline keyprovider encryption/decryption probe")
        sealed = _manifest(encrypted)["layers"][0]
        restored = _manifest(decrypted)["layers"][0]
        if (
            sealed["mediaType"] != LAYER + "+encrypted"
            or not sealed.get("annotations", {}).get("org.opencontainers.image.enc.keys.provider.probe")
            or operations.read_text().splitlines() != ["keywrap", "keyunwrap"]
            or restored["mediaType"] != LAYER
            or (decrypted / "blobs" / "sha256" / restored["digest"].split(":")[1]).read_bytes() != plaintext
        ):
            raise ValueError("Installed Skopeo did not complete the offline keyprovider round trip")


if __name__ == "__main__":
    verify_keyprovider(sys.argv[1])
    print("Skopeo keyprovider encryption/decryption verified with an offline public fixture")
