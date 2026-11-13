"""Build pinned macOS admission-test assets in a new private directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

MODULES = {
    "k8s.io/kubernetes": ("v1.36.4", "h1:08GT0ZOMtyCcRnslyvnpWurf1wm20KHbC/aRS+0LxSo="),
    "go.etcd.io/etcd/server/v3": ("v3.6.8", "h1:U2strdSEy1U8qcSzRIdkYpvOPtBy/9i/IfaaCI9flZ4="),
}
SOURCES = {
    "kube-apiserver": (
        "k8s.io/kubernetes",
        "cmd/kube-apiserver/apiserver.go",
        "8471f518c26f09b7670521522ecd7599bc5317a773f7b779608d3dfd22ced0ec",
    ),
    "etcd": (
        "go.etcd.io/etcd/server/v3",
        "main.go",
        "824bce45d763aaa6227e4b7557cbd222a7e039fe23db2d9a85ef98167eaef4e3",
    ),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opscode", type=Path, required=True, help="Explicit reviewed Opscode checkout")
    parser.add_argument("--output", type=Path, required=True, help="New absolute private asset directory")
    parser.add_argument("--go", default=shutil.which("go"), help="Go toolchain executable")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("This fixture builds darwin/arm64 assets; use the existing Linux qualification harness elsewhere")
    if not args.go or not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
        parser.error("Choose a Go executable and a new absolute output directory")
    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    root = args.output.resolve(strict=True)
    go = str(Path(args.go).resolve(strict=True))
    env = os.environ.copy()
    env.update(
        GOOS="darwin",
        GOARCH="arm64",
        CGO_ENABLED="0",
        GOWORK="off",
        GOMAXPROCS="2",
        GOMEMLIMIT="1GiB",
        GOFLAGS="-p=1",
        GOPROXY="https://proxy.golang.org",
        GOSUMDB="sum.golang.org",
    )
    started, checks, code = time.monotonic(), [], 1

    def run(arguments, *, capture=False):
        log = root / ("step-" + str(len(checks)) + ".log")
        with log.open("xb") as output:
            result = subprocess.run(
                [go, *arguments],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE if capture else output,
                stderr=output,
                timeout=3600,
            )
            if capture:
                output.write(result.stdout)
        checks.append({"command": arguments, "exitCode": result.returncode, "log": str(log)})
        if result.returncode:
            raise RuntimeError("Pinned asset build failed; inspect " + str(log))
        return result.stdout.decode() if capture else None

    try:
        toolchain = run(["version"], capture=True).strip()
        helper = args.opscode.resolve(strict=True) / "kubernetes/nvke/kubelet-options-helper/kubernetes-1.36.4"
        input_hashes = {}
        for name in ("go.mod", "go.sum"):
            source = helper / name
            input_hashes[name] = digest(source)
            shutil.copyfile(source, root / name)
        modules = {}
        for name, (version, checksum) in MODULES.items():
            selected = json.loads(run(["mod", "download", "-json", name + "@" + version], capture=True))
            assert selected["Path"] == name and selected["Version"] == version and selected["Sum"] == checksum
            modules[name] = selected
        for binary, (module, relative, checksum) in SOURCES.items():
            source = Path(modules[module]["Dir"]) / relative
            assert digest(source) == checksum, "Pinned upstream entrypoint changed"
            target = root / "src" / binary / "main.go"
            target.parent.mkdir(parents=True, mode=0o700)
            shutil.copyfile(source, target)
        run(["mod", "edit", "-require=go.etcd.io/etcd/server/v3@v3.6.8"])
        run(["mod", "tidy"])
        run(["mod", "verify"])
        for name, expected in MODULES.items():
            assert tuple(run(["list", "-m", "-f", "{{.Version}} {{.Sum}}", name], capture=True).split()) == expected
        frozen = {name: digest(root / name) for name in ("go.mod", "go.sum")}
        for binary in SOURCES:
            arguments = ["build", "-mod=readonly", "-trimpath"]
            if binary == "kube-apiserver":
                arguments.append("-ldflags=-X k8s.io/component-base/version.gitVersion=v1.36.4")
            run([*arguments, "-o", str(root / binary), "./src/" + binary])
            (root / binary).chmod(0o500)
        assert frozen == {name: digest(root / name) for name in frozen}
        assert all(digest(root / "src" / name / "main.go") == row[2] for name, row in SOURCES.items())
        assert input_hashes == {name: digest(helper / name) for name in input_hashes}
        api_version = subprocess.check_output([str(root / "kube-apiserver"), "--version"], text=True).strip()
        etcd_version = subprocess.check_output([str(root / "etcd"), "--version"], text=True)
        assert api_version == "Kubernetes v1.36.4" and "etcd Version: 3.6.8" in etcd_version
        manifest = {
            "format": 1,
            "platform": "darwin/arm64",
            "kubernetesVersion": "v1.36.4",
            "etcdVersion": "v3.6.8",
            "toolchain": toolchain,
            "moduleChecksums": {name: row[1] for name, row in MODULES.items()},
            "inputHelperSha256": input_hashes,
            "sourceMainSha256": {name: row[2] for name, row in SOURCES.items()},
            "goModSha256": frozen["go.mod"],
            "goSumSha256": frozen["go.sum"],
            "binaries": {
                name: {"sha256": digest(root / name), "bytes": (root / name).stat().st_size} for name in SOURCES
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        code = 0
    except BaseException as error:
        checks.append({"failureType": type(error).__name__})
        raise
    finally:
        report = {
            "exitCode": code,
            "assetRoot": str(root),
            "checks": checks,
            "elapsedSeconds": time.monotonic() - started,
        }
        (root / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
        (root / "build.exit").write_text(str(code) + "\n")


if __name__ == "__main__":
    main()
