"""Run in the init container; download, verify and install the immutable bundle."""

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def install(config, app_root="/app", data_root="/data"):
    request = urllib.request.Request(
        config["url"], headers={"Authorization": "BuilderArtifact " + config["token"]}
    )

    # Never forward the artifact grant to a redirect destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    with tempfile.TemporaryFile() as output:
        digest = hashlib.sha256()
        size = 0
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > config["bytes"]:
                    raise ValueError("artifact exceeds declared size")
                digest.update(chunk)
                output.write(chunk)
        if size != config["bytes"] or digest.hexdigest() != config["sha256"]:
            raise ValueError("artifact integrity check failed")
        output.seek(0)
        with zipfile.ZipFile(output) as archive:
            if sum(e.file_size for e in archive.infolist()) > config["expanded_bytes"]:
                raise ValueError("artifact exceeds declared expanded size")
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename)
                if (
                    path.is_absolute()
                    or len(path.parts) < 2
                    or path.parts[0] not in ("app", "data")
                    or any(p in (".", "..") for p in path.parts)
                ):
                    raise ValueError("invalid artifact path")
                root = Path(app_root if path.parts[0] == "app" else data_root)
                target = root.joinpath(*path.parts[1:])
                if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
                    raise ValueError("artifact path escapes volume")
                if path.parts[0] == "data" and target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(dir=target.parent, prefix=".builder-")
                try:
                    with os.fdopen(fd, "wb") as dest, archive.open(entry) as src:
                        shutil.copyfileobj(src, dest)
                    os.chmod(name, 0o644)
                    os.replace(name, target)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)


if __name__ == "__main__":
    install(json.loads(Path("/artifact/config").read_text()))
