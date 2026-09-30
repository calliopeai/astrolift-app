"""Pure manifests for the supervised managed IDE runtime (#1971)."""

from __future__ import annotations

import re
from copy import deepcopy

from astrolift_agents.services.agent_box import FINISHED_JOB_TTL_SECONDS, _merge_env, box_secret_name
from astrolift_dispatch.agent_secrets import env_var_entries, secret_env_entries
from astrolift_dispatch.pod_hardening import harden_agent_pod

MANAGED_RUNTIME = "calliope-managed-ide"
RUNTIME_CONTAINER = "runtime"
WORKSPACE_PATH = "/workspace"
STATE_PATH = "/state"
AUTHORITY_FILE = "/run/astrolift-authority/authority.json"
_SERVER_SCRIPTS = "/opt/calliope/scripts/managed-runtime"
_SERVER_NODE = "/opt/calliope/vscode-server/node"
_BROWSER_SCRIPTS = "/opt/calliope-managed-runtime"


def _immutable_image(image: str) -> str:
    if not isinstance(image, str) or not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Managed runtime images require an explicit immutable sha256 digest.")
    return image


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Managed runtime resource names must be Kubernetes DNS labels.")
    return value


def _labels(box) -> dict[str, str]:
    return {
        "astrolift.dev/workload-kind": "agent-box",
        "astrolift.dev/agent-box": str(box.guid),
        "astrolift.dev/app": str(box.guid),
        "astrolift.dev/runtime": MANAGED_RUNTIME,
    }


def _security() -> dict:
    return {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }


def _probes(command: list[str]) -> dict:
    return {
        "startupProbe": {
            "exec": {"command": list(command)},
            "periodSeconds": 5,
            "timeoutSeconds": 3,
            "failureThreshold": 36,
        },
        "readinessProbe": {
            "exec": {"command": list(command)},
            "periodSeconds": 5,
            "timeoutSeconds": 3,
            "failureThreshold": 2,
        },
        "livenessProbe": {
            "exec": {"command": list(command)},
            "periodSeconds": 5,
            "timeoutSeconds": 3,
            "failureThreshold": 3,
        },
    }


def render_managed_box_pvc(
    *, box, namespace: str, claim_name: str, storage_class_name: str, capacity: str
) -> dict:
    if not storage_class_name or not isinstance(storage_class_name, str):
        raise ValueError("Managed runtime storage requires an explicit StorageClass.")
    if not isinstance(capacity, str) or not re.fullmatch(r"[1-9][0-9]*(?:Mi|Gi|Ti)", capacity):
        raise ValueError("Managed runtime storage capacity requires positive Mi, Gi or Ti units.")
    # No Job owner reference: retiring a pod must not garbage-collect its history.
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": _name(claim_name), "namespace": _name(namespace), "labels": _labels(box)},
        "spec": {
            "accessModes": ["ReadWriteOncePod"],
            "volumeMode": "Filesystem",
            "storageClassName": storage_class_name,
            "resources": {"requests": {"storage": capacity}},
        },
    }


def render_managed_box_job(
    *,
    box,
    image: str,
    browser_image: str,
    namespace: str,
    job_name: str,
    claim_name: str,
    authority_secret_name: str,
    secret_env_names: list[str] | None = None,
    model_service_account: str = "",
    model_env: list[dict] | None = None,
    spec_env: list[dict] | None = None,
    model_gateway=None,
) -> dict:
    """Render only; the lifecycle resolves routing, verifies storage and certifies the pod."""
    spec = box.environment_spec
    if getattr(spec, "runtime", None) != MANAGED_RUNTIME or not getattr(spec, "image_tag", ""):
        raise ValueError("Managed boxes require the explicit managed runtime and image tag.")
    image, browser_image = _immutable_image(image), _immutable_image(browser_image)
    namespace, job_name = _name(namespace), _name(job_name)
    claim_name, authority_secret_name = _name(claim_name), _name(authority_secret_name)
    secret_name = box_secret_name(job_name)
    common_env = [
        {"name": "CALLIOPE_MANAGED_STATE_ROOT", "value": STATE_PATH},
        {"name": "CALLIOPE_MANAGED_WORKSPACE", "value": WORKSPACE_PATH},
        {"name": "CALLIOPE_MANAGED_PORT", "value": "8071"},
    ]
    platform_env = [
        *common_env,
        {"name": "CALLIOPE_MANAGED_SERVER_ROOT", "value": "/opt/calliope/vscode-server"},
        {"name": "ASTROLIFT_AGENT_BOX", "value": "1"},
        {"name": "ASTROLIFT_AGENT_BOX_GUID", "value": str(box.guid)},
        {"name": "ASTROLIFT_CONTAINER_NAME", "value": RUNTIME_CONTAINER},
        {"name": "ASTROLIFT_MANAGED_AUTHORITY_FILE", "value": AUTHORITY_FILE},
        *[
            {"name": name, "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": field}}}
            for name, field in (
                ("ASTROLIFT_POD_UID", "metadata.uid"),
                ("ASTROLIFT_POD_NAME", "metadata.name"),
                ("ASTROLIFT_POD_NAMESPACE", "metadata.namespace"),
            )
        ],
    ]
    state_mount = {"name": "managed-data", "mountPath": STATE_PATH, "subPath": "state"}
    runtime = {
        "name": RUNTIME_CONTAINER,
        "image": image,
        "command": ["/bin/sh", f"{_SERVER_SCRIPTS}/server.sh"],
        "workingDir": WORKSPACE_PATH,
        "env": deepcopy(
            _merge_env(
                model_env or [],
                env_var_entries(getattr(spec, "env_vars", None)),
                spec_env or [],
                secret_env_entries(
                    secret_name, [{"env_var": name} for name in sorted(set(secret_env_names or []))]
                ),
            )
        ),
        "volumeMounts": [
            dict(state_mount),
            {"name": "managed-data", "mountPath": WORKSPACE_PATH, "subPath": "workspace"},
            {"name": "runtime-authority", "mountPath": "/run/astrolift-authority", "readOnly": True},
        ],
        **_probes([_SERVER_NODE, f"{_SERVER_SCRIPTS}/runtime.cjs"]),
    }
    if model_gateway is not None:
        model_gateway.wire_container(runtime, secret_name=secret_name)
    runtime["env"] = _merge_env(
        [entry for entry in runtime["env"] if not entry["name"].startswith("CALLIOPE_MANAGED_")],
        platform_env,
    )
    workbench = {
        "name": "workbench",
        "image": browser_image,
        "command": ["node", f"{_BROWSER_SCRIPTS}/browser.cjs"],
        "env": deepcopy(common_env),
        "volumeMounts": [dict(state_mount), {"name": "browser-shm", "mountPath": "/dev/shm"}],
        **_probes(["node", f"{_BROWSER_SCRIPTS}/runtime.cjs"]),
    }
    init = {
        "name": "managed-directories",
        "image": image,
        "command": ["/bin/sh", "-ec", "umask 077; mkdir -p /data/state /data/workspace"],
        "volumeMounts": [{"name": "managed-data", "mountPath": "/data"}],
        "securityContext": _security(),
        "resources": {
            "requests": {"cpu": "100m", "memory": "64Mi"},
            "limits": {"cpu": "500m", "memory": "128Mi"},
        },
    }
    pod = {
        "restartPolicy": "Never",
        "terminationGracePeriodSeconds": 15,
        "containers": [runtime, workbench],
        "initContainers": [init],
        "volumes": [
            {"name": "managed-data", "persistentVolumeClaim": {"claimName": claim_name}},
            {
                "name": "runtime-authority",
                "projected": {
                    "defaultMode": 0o440,
                    "sources": [
                        {
                            "secret": {
                                "name": authority_secret_name,
                                "items": [{"key": "authority.json", "path": "authority.json"}],
                            }
                        }
                    ],
                },
            },
            {"name": "browser-shm", "emptyDir": {"medium": "Memory", "sizeLimit": "512Mi"}},
        ],
        **({"serviceAccountName": model_service_account} if model_service_account else {}),
    }
    harden_agent_pod(pod, spec=spec)
    pod["securityContext"].update(
        {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "runAsGroup": 1000,
            "fsGroup": 1000,
            "fsGroupChangePolicy": "OnRootMismatch",
        }
    )
    for container, cpu, memory, cpu_limit, memory_limit in (
        (runtime, "1", "1536Mi", "2", "3Gi"),
        (workbench, "500m", "768Mi", "1", "1536Mi"),
    ):
        container["securityContext"] = _security()
        container["resources"]["requests"].update({"cpu": cpu, "memory": memory})
        container["resources"]["limits"].update({"cpu": cpu_limit, "memory": memory_limit})
    labels = _labels(box)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": namespace, "labels": dict(labels)},
        "spec": {
            "backoffLimit": 0,
            "completions": 1,
            "parallelism": 1,
            "ttlSecondsAfterFinished": FINISHED_JOB_TTL_SECONDS,
            "template": {"metadata": {"labels": dict(labels)}, "spec": pod},
        },
    }
