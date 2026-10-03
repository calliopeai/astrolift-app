"""Offline render of exact reviewed collector bytes, with no cluster effects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from aws._cloudwatch_collector import COMPONENT_KEY, NAMESPACE, PIN, PROFILE, staged_component

if TYPE_CHECKING:
    from _sdk.cluster import BootstrapComponent

HELM_PIN = json.loads((PROFILE / "helm-runtime.json").read_text())
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
ALLOWED_KINDS = {"ServiceAccount", "ClusterRole", "ClusterRoleBinding", "ConfigMap", "DaemonSet", "Service"}


class CollectorRenderError(RuntimeError):
    """Safe refusal; no subprocess output or rendered data in diagnostics."""


def _checked_values(component: BootstrapComponent) -> dict[str, Any]:
    try:
        values = component.helm_values
        role = values["serviceAccount"]["annotations"]["eks.amazonaws.com/role-arn"]
        region, group = values["cloudwatch"]["region"], values["cloudwatch"]["logGroup"]
        match = re.fullmatch(
            r"arn:(?:aws|aws-cn|aws-us-gov):iam::\d{12}:role/astrolift/astrolift-"
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-fluent-bit",
            role,
        )
        valid = (
            component.key == COMPONENT_KEY
            and component.chart_name == PIN["name"]
            and component.chart_repo_url == PIN["repository"]
            and component.chart_version == PIN["version"]
            and match is not None
            and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region)
            and group == f"/astrolift/clusters/{match[1]}/pods"
            and values == staged_component(irsa_role_arn=role, region=region, log_group=group).helm_values
        )
        if valid:
            return values
    except (KeyError, TypeError, AttributeError, ValueError):
        pass
    raise CollectorRenderError("Collector values differ from the prepared reviewed profile")


def render_collector(
    component: BootstrapComponent,
    *,
    archive: bytes,
    namespace: str,
    release_name: str,
    helm_binary: str = "/usr/local/bin/helm",
) -> list[dict[str, Any]]:
    """Pure renderer port; caller still owes authority, installation and ingestion."""
    if (
        namespace != NAMESPACE
        or not isinstance(release_name, str)
        or len(release_name) > 53
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", release_name)
    ):
        raise CollectorRenderError("Collector render identity is invalid")
    if not archive or len(archive) > MAX_ARCHIVE_BYTES or hashlib.sha256(archive).hexdigest() != PIN["archiveSha256"]:
        raise CollectorRenderError("Collector archive differs from the reviewed checksum")
    values = _checked_values(component)
    try:
        with tempfile.TemporaryDirectory(prefix="astrolift-collector-render-") as scratch:
            root = Path(scratch)
            environment = {
                **os.environ,
                "HELM_CACHE_HOME": str(root / "cache"),
                "HELM_CONFIG_HOME": str(root / "config"),
                "HELM_DATA_HOME": str(root / "data"),
                "HELM_PLUGINS": str(root / "plugins"),
            }
            chart, value_file = root / "chart.tgz", root / "values.yaml"
            chart.write_bytes(archive)
            value_file.write_text(yaml.safe_dump(values))

            def execute(arguments: list[str], *, timeout: int) -> bytes:
                with (root / "stdout").open("w+b") as output, (root / "stderr").open("w+b") as diagnostics:
                    result = subprocess.run(
                        [helm_binary, *arguments],
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=diagnostics,
                        cwd=root,
                        env=environment,
                        timeout=timeout,
                        check=False,
                    )
                    if result.returncode or output.tell() > MAX_OUTPUT_BYTES or diagnostics.tell() > MAX_OUTPUT_BYTES:
                        raise CollectorRenderError("Pinned Helm collector render failed")
                    output.seek(0)
                    return output.read(MAX_OUTPUT_BYTES + 1)

            version = execute(["version", "--short"], timeout=10).decode().strip().split("+")[0]
            if version != HELM_PIN["version"]:
                raise CollectorRenderError("The reviewed Helm renderer is unavailable")
            rendered = execute(
                [
                    "template",
                    release_name,
                    str(chart),
                    "--namespace",
                    namespace,
                    "--values",
                    str(value_file),
                    "--skip-tests",
                    "--no-hooks",
                ],
                timeout=30,
            )
            manifests = [item for item in yaml.safe_load_all(rendered) if item is not None]
    except CollectorRenderError:
        raise
    except Exception:
        raise CollectorRenderError("Pinned Helm collector render failed") from None
    if not manifests or len(manifests) > 16:
        raise CollectorRenderError("Collector rendered an invalid resource set")
    seen = set()
    for resource in manifests:
        if not isinstance(resource, dict) or resource.get("kind") not in ALLOWED_KINDS:
            raise CollectorRenderError("Collector rendered an unsupported resource")
        metadata = resource.get("metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
            raise CollectorRenderError("Collector rendered an invalid resource identity")
        kind = resource["kind"]
        expected_namespace = None if kind in {"ClusterRole", "ClusterRoleBinding"} else namespace
        if metadata.get("namespace") != expected_namespace:
            raise CollectorRenderError("Collector rendered a foreign namespace")
        identity = (kind, metadata["name"])
        annotations = metadata.get("annotations") or {}
        if not isinstance(annotations, dict):
            raise CollectorRenderError("Collector rendered invalid resource annotations")
        if identity in seen or "helm.sh/hook" in annotations:
            raise CollectorRenderError("Collector rendered a duplicate or hook resource")
        seen.add(identity)
    return manifests
