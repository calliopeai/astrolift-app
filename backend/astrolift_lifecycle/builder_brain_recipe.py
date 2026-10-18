"""Trusted runtime recipes; an uploaded Dockerfile is only an ordinary source file."""

import json
import re
import shlex


class RecipeError(ValueError):
    pass


def recipe(dev):
    from astrolift_lifecycle.builder_views import _path_error

    versions = {
        "python": (
            "python",
            "3.11-slim",
            "if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi",
        ),
        "node": (
            "node",
            "20-slim",
            "if [ -f package-lock.json ]; then npm ci; elif [ -f package.json ]; then npm install; fi",
        ),
        "ruby": ("ruby", "3.3-slim", "if [ -f Gemfile ]; then bundle install; fi"),
        "go": ("golang", "1.22-alpine", "if [ -f go.mod ]; then go mod download; fi"),
        "static": ("nginx", "alpine", ""),
    }
    if dev.runtime not in versions:
        raise RecipeError("unsupported builder runtime")
    image, version, install = versions[dev.runtime]
    if dev.runtime_version and dev.runtime != "static":
        if not re.fullmatch(r"[0-9][A-Za-z0-9._-]{0,31}", dev.runtime_version):
            raise RecipeError("invalid runtime version")
        version = dev.runtime_version + ("-" + version.split("-", 1)[1] if "-" in version else "")
    port = dev.port
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise RecipeError("source-hosted apps require an unprivileged port between 1024 and 65535")
    command = (dev.start_command or "").strip()
    if dev.runtime != "static" and not command:
        raise RecipeError("start_command is required")
    if not isinstance(dev.env_vars, dict) or any(
        not isinstance(k, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
        or k.startswith(("BRAIN_", "ASTROLIFT_"))
        or not isinstance(v, str)
        for k, v in dev.env_vars.items()
    ):
        raise RecipeError("invalid or platform-reserved environment variable")
    # Runtime env stays in the manifest, never in the image/build context.
    dockerfile = [
        f"FROM {image}:{version}",
        "WORKDIR /app",
        "COPY app/ /app/",
        "COPY data/ /opt/astrolift-seed/",
        "COPY platform/start.sh /opt/astrolift-start.sh",
    ]
    if install:
        dockerfile.append("RUN " + install)
    dockerfile += ["ENV HOME=/tmp GOCACHE=/tmp/go-cache", "USER 10001:10001", f"EXPOSE {port}"]
    dockerfile.append("ENTRYPOINT " + json.dumps(["/bin/sh", "/opt/astrolift-start.sh"]))
    entrypoint = ["#!/bin/sh", "set -eu"]
    if dev.data_file_path:
        if _path_error(dev.data_file_path):
            raise RecipeError("invalid data file path")
        path = shlex.quote("/data/" + dev.data_file_path)
        seed = shlex.quote("/opt/astrolift-seed/" + dev.data_file_path)
        entrypoint += [
            f"if [ ! -e {path} ]; then",
            f'  mkdir -p "$(dirname {path})"',
            f"  cp {seed} {path}",
            "fi",
        ]
    if dev.runtime == "static":
        # No root-owned cache/pid/log writes; all scratch is the declared /tmp.
        config = (
            "pid /tmp/nginx.pid; events {} http { "
            "access_log /dev/stdout; error_log /dev/stderr; "
            "client_body_temp_path /tmp/client; proxy_temp_path /tmp/proxy; "
            "fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi; "
            f"server {{ listen {port}; root /app; }} }}"
        )
        entrypoint += [
            "printf %s " + shlex.quote(config) + " > /tmp/nginx.conf",
            "exec nginx -c /tmp/nginx.conf -g 'daemon off;'",
        ]
    else:
        entrypoint.append("exec /bin/sh -c " + shlex.quote(command))
    return {"dockerfile": "\n".join(dockerfile) + "\n", "entrypoint": "\n".join(entrypoint) + "\n"}


def manifest(*, app_slug, brain_slug, image, admission):
    import tomllib

    import tomli_w

    from astrolift_registry.brain_apps import brain_app_manifest

    raw = tomllib.loads(
        brain_app_manifest(slug=app_slug, brain_slug=brain_slug, image=image, port=admission["port"])
    )
    workload = raw["workloads"][0]
    workload["fs_group"] = 10001
    workload["volumes"] = [
        {"name": "scratch", "kind": "empty_dir", "mount_path": "/tmp", "size_limit": "1Gi"}
    ]
    env = dict(admission["env_vars"])
    if admission["data_file_path"]:
        workload["volumes"].append(
            {"name": "data", "kind": "empty_dir", "mount_path": "/data", "size_limit": "1Gi"}
        )
        env.update(ASTROLIFT_DATA_DIR="/data", ASTROLIFT_DATA_FILE="/data/" + admission["data_file_path"])
    workload["containers"][0]["env"] = env
    return tomli_w.dumps(raw)
