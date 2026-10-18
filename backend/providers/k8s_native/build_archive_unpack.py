"""Standalone build init-container program. No Django or cloud dependencies."""

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def install(config, root="/workspace"):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    request = urllib.request.Request(config["url"], headers={"Authorization": "BuilderBuildArchive " + config["token"]})
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as output:
        digest = hashlib.sha256()
        size = 0
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > config["size_bytes"]:
                    raise ValueError("archive exceeds declared size")
                digest.update(chunk)
                output.write(chunk)
        if size != config["size_bytes"] or digest.hexdigest() != config["sha256"]:
            raise ValueError("archive integrity check failed")
        output.seek(0)
        with zipfile.ZipFile(output) as archive:
            entries = archive.infolist()
            if sum(e.file_size for e in entries) > config["expanded_bytes"]:
                raise ValueError("archive exceeds expanded size")
            seen = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    path.is_absolute()
                    or len(path.parts) < 2
                    or path.parts[0] not in ("app", "data")
                    or any(part in (".", "..") for part in path.parts)
                    or str(path) != entry.filename
                    or entry.filename in seen
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("invalid archive path")
                seen.add(entry.filename)
                target = destination.joinpath(*path.parts)
                if not target.resolve().is_relative_to(destination.resolve()) or target.is_symlink():
                    raise ValueError("archive path escapes context")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as src, target.open("wb") as dest:
                    shutil.copyfileobj(src, dest)
                os.chmod(target, 0o644)
    (destination / "app").mkdir(exist_ok=True)
    (destination / "data").mkdir(exist_ok=True)
    platform = destination / "platform"
    platform.mkdir(exist_ok=True)
    (platform / "Dockerfile").write_text(config["dockerfile"])
    (platform / "start.sh").write_text(config["entrypoint"])


if __name__ == "__main__":
    install(json.loads(Path("/archive/config").read_text()))
