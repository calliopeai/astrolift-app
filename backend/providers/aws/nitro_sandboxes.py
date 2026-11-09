"""EKS Nitro Sandbox discovery and admission checks; no host-side VM API."""

from __future__ import annotations

from typing import Any

from _sdk.cluster import ApplyError, apply_error_message, classify_apply_error

RUNTIME_MODES = {"isolated-sandbox": "isolated", "confidential-sandbox": "confidential"}
POLICY_ANNOTATION = "sandbox.amazonaws.com/policy"
NETWORK_ISOLATED_ANNOTATION = "sandbox.amazonaws.com/network-isolated"
RUNTIME_KIND = "node.k8s.io/v1/RuntimeClass"


def discover_runtimes(resources: list[dict[str, Any]]) -> dict[str, Any]:
    """Report installed configuration, without claiming workload attestation."""
    names = {
        str((resource.get("metadata") or {}).get("name", ""))
        for resource in resources
        if resource.get("handler") == (resource.get("metadata") or {}).get("name")
    }
    runtimes = [{"name": name, "mode": mode} for name, mode in RUNTIME_MODES.items() if name in names]
    return {"status": "detected" if runtimes else "not_detected", "runtime_classes": runtimes}


def _pod_template(manifest: dict[str, Any]) -> dict[str, Any] | None:
    kind = manifest.get("kind")
    if kind == "Pod":
        return manifest
    spec = manifest.get("spec") or {}
    if kind == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    if kind in {"CronJob", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "ReplicationController"}:
        template = spec.get("template")
        return template if isinstance(template, dict) else None
    return None


def _validate_pod(pod: dict[str, Any], runtime: str) -> None:
    spec = pod.get("spec") or {}
    annotations = (pod.get("metadata") or {}).get("annotations") or {}
    if spec.get("hostNetwork"):
        raise ValueError("Nitro Sandboxes cannot use hostNetwork; remove it from the pod spec")
    if spec.get("hostUsers") is False:
        raise ValueError("Nitro Sandboxes do not support hostUsers=false")
    if runtime == "confidential-sandbox" and not str(annotations.get(POLICY_ANNOTATION) or "").strip():
        raise ValueError(
            "confidential-sandbox requires sandbox.amazonaws.com/policy generated from the final pod spec; "
            "configure KMS attestation conditions before releasing secrets"
        )
    for container in [*(spec.get("initContainers") or []), *(spec.get("containers") or [])]:
        resources = container.get("resources") or {}
        for field in ("requests", "limits"):
            if any(key in (resources.get(field) or {}) for key in ("nvidia.com/gpu", "amd.com/gpu")):
                raise ValueError("Nitro Sandboxes do not support GPU device passthrough")
        if annotations.get(NETWORK_ISOLATED_ANNOTATION) == "true":
            for name in ("livenessProbe", "readinessProbe", "startupProbe"):
                probe = container.get(name) or {}
                if "httpGet" in probe or "tcpSocket" in probe or "grpc" in probe:
                    raise ValueError(
                        "network-isolated Nitro Sandboxes require exec probes; network probes cannot succeed"
                    )


def preflight_manifests(client: Any, manifests: list[dict[str, Any]], namespace: str) -> list[ApplyError]:
    """Validate the entire batch before any writes, including dry-run applies."""
    errors = []
    checked: dict[str, dict[str, Any] | None] = {}
    for manifest in manifests:
        pod = _pod_template(manifest)
        if pod is None:
            continue
        runtime = (pod.get("spec") or {}).get("runtimeClassName")
        if not isinstance(runtime, str) or runtime not in RUNTIME_MODES:
            continue
        meta = manifest.get("metadata") or {}
        try:
            _validate_pod(pod, runtime)
            if runtime not in checked:
                checked[runtime] = client.get(kind=RUNTIME_KIND, namespace=None, name=runtime)
            resource = checked[runtime]
            if resource is None or resource.get("handler") != runtime:
                raise ValueError(
                    f"Nitro Sandbox RuntimeClass {runtime!r} is absent or has an unexpected handler; "
                    "use EKS Auto Mode or install the AWS sandbox runtime on supported nodes"
                )
        except Exception as exc:
            errors.append(
                ApplyError(
                    kind=str(manifest.get("kind", "")),
                    name=str(meta.get("name", "")),
                    namespace=str(meta.get("namespace") or namespace),
                    exception_type=type(exc).__name__,
                    exception_message=apply_error_message(exc),
                    is_retryable=classify_apply_error(exc),
                )
            )
    return errors
