"""Prepare pinned upstream CPU source; never builds or publishes an image."""

import argparse
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path


def prepare(archive: bytes, destination: Path, spec: dict) -> Path:
    if hashlib.sha256(archive).hexdigest() != spec["archive_sha256"]:
        raise ValueError("Upstream archive digest mismatch")
    if destination.exists():
        raise ValueError("Build destination must not exist")
    expected_root = "vllm-" + spec["upstream_revision"]
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        for member in source.getmembers():
            parts = Path(member.name).parts
            if (
                not parts
                or parts[0] != expected_root
                or ".." in parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("Unexpected upstream archive member")
        source.extractall(destination, filter="data")
    root = destination / expected_root
    dockerfile = root / "docker/Dockerfile.cpu"
    text = dockerfile.read_text()
    base = "FROM ubuntu:22.04 AS base-common"
    if text.count(base) != 1:
        raise ValueError("Upstream Dockerfile base changed")
    text = text.replace(base, f'FROM {spec["ubuntu_amd64_image"]} AS base-common')
    text += (
        '\nENV VLLM_CPU_OMP_THREADS_BIND=nobind OMP_NUM_THREADS=1 '
        'VLLM_CPU_NUM_OF_RESERVED_CPU=0 VLLM_CPU_SGL_KERNEL=0\n'
        f'LABEL ai.astrolift.vllm.source="{spec["upstream_revision"]}" '
        'ai.astrolift.vllm.cpu-isa="avx2"\n'
    )
    wheel = "VLLM_TARGET_DEVICE=cpu python3 setup.py bdist_wheel"
    if text.count(wheel) != 1:
        raise ValueError("Upstream CPU wheel command changed")
    text = text.replace(
        wheel, f'VLLM_VERSION_OVERRIDE={spec["runtime_package"]} ' + wheel
    )
    dockerfile.write_text(text)
    return root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    spec = json.loads(Path(__file__).with_name("source.json").read_text())
    if args.archive:
        archive = args.archive.read_bytes()
    else:
        url = (
            "https://codeload.github.com/vllm-project/vllm/tar.gz/"
            + spec["upstream_revision"]
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            archive = response.read(32 * 1024 * 1024 + 1)
        if len(archive) > 32 * 1024 * 1024:
            raise ValueError("Upstream source exceeds bound")
    print(prepare(archive, args.destination, spec))


if __name__ == "__main__":
    main()
