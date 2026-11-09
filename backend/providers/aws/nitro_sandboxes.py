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


def _object(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _annotations(metadata: Any, path: str) -> dict[str, Any]:
    annotations = _object(_object(metadata, path).get("annotations"), f"{path}.annotations")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in annotations.items()):
        raise ValueError(f"{path}.annotations must contain string keys and values")
    return annotations


def _pod_template(manifest: dict[str, Any]) -> dict[str, Any] | None:
    kind = manifest.get("kind")
    if kind == "List":
        raise ValueError(
            "Kubernetes List is not supported by this apply path; submit its items as individual manifests"
        )
    if kind == "Pod":
        return manifest
    if kind not in {"CronJob", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "ReplicationController"}:
        return None
    spec = _object(manifest.get("spec"), "spec")
    if kind == "CronJob":
        job = _object(spec.get("jobTemplate"), "spec.jobTemplate")
        spec = _object(job.get("spec"), "spec.jobTemplate.spec")
    if kind in {"CronJob", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "ReplicationController"}:
        template = spec.get("template")
        return _object(template, "pod template") if template is not None else None
    return None


def _validate_pod(pod: dict[str, Any], runtime: str, annotations: dict[str, Any]) -> None:
    spec = _object(pod.get("spec"), "pod.spec")
    if spec.get("hostNetwork"):
        raise ValueError("Nitro Sandboxes cannot use hostNetwork; remove it from the pod spec")
    if spec.get("hostUsers") is False:
        raise ValueError("Nitro Sandboxes do not support hostUsers=false")
    if runtime == "confidential-sandbox" and not str(annotations.get(POLICY_ANNOTATION) or "").strip():
        raise ValueError(
            "confidential-sandbox requires sandbox.amazonaws.com/policy generated from the final pod spec; "
            "configure KMS attestation conditions before releasing secrets"
        )
    network_isolated = annotations.get(NETWORK_ISOLATED_ANNOTATION)
    if network_isolated is not None and network_isolated not in {"true", "false"}:
        raise ValueError("sandbox.amazonaws.com/network-isolated must be the string true or false")
    for field in ("initContainers", "containers"):
        containers = spec.get(field) or []
        if not isinstance(containers, list):
            raise ValueError(f"pod.spec.{field} must be an array")
        for container in containers:
            container = _object(container, f"pod.spec.{field}[]")
            resources = _object(container.get("resources"), "container.resources")
            for resource_field in ("requests", "limits"):
                quantities = _object(resources.get(resource_field), f"container.resources.{resource_field}")
                if any(str(key).startswith(("nvidia.com/gpu", "nvidia.com/mig-", "amd.com/gpu")) for key in quantities):
                    raise ValueError("Nitro Sandboxes do not support GPU device passthrough")
            if network_isolated == "true":
                for name in ("livenessProbe", "readinessProbe", "startupProbe"):
                    probe = _object(container.get(name), f"container.{name}")
                    if "httpGet" in probe or "tcpSocket" in probe or "grpc" in probe:
                        raise ValueError(
                            "network-isolated Nitro Sandboxes require exec probes; network probes cannot succeed"
                        )


def preflight_manifests(client: Any, manifests: list[dict[str, Any]], namespace: str) -> list[ApplyError]:
    """Validate the entire batch before any writes, including dry-run applies."""
    errors = []
    checked: dict[str, dict[str, Any] | None] = {}
    for manifest in manifests:
        meta: dict[str, Any] = {}
        kind = ""
        try:
            manifest = _object(manifest, "manifest")
            kind = manifest.get("kind", "")
            if not isinstance(kind, str):
                kind = ""
                raise ValueError("manifest.kind must be a string")
            meta = _object(manifest.get("metadata"), "metadata")
            outer_annotations = _annotations(meta, "metadata")
            pod = _pod_template(manifest)
            if pod is None:
                if any(key.startswith("sandbox.amazonaws.com/") for key in outer_annotations):
                    raise ValueError("Nitro Sandbox annotations must be on a supported pod template")
                continue
            annotations = _annotations(pod.get("metadata"), "pod.metadata")
            spec = _object(pod.get("spec"), "pod.spec")
            runtime = spec.get("runtimeClassName")
            if runtime is not None and not isinstance(runtime, str):
                raise ValueError("pod.spec.runtimeClassName must be a string")
            explicit_sandbox = any(key.startswith("sandbox.amazonaws.com/") for key in annotations)
            if runtime not in RUNTIME_MODES:
                if explicit_sandbox:
                    raise ValueError(
                        "Nitro Sandbox annotations require isolated-sandbox or confidential-sandbox runtimeClassName"
                    )
                continue
            if pod is not manifest and any(key.startswith("sandbox.amazonaws.com/") for key in outer_annotations):
                raise ValueError("Nitro Sandbox annotations must be on the pod template, not controller metadata")
            _validate_pod(pod, runtime, annotations)
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
                    kind=kind,
                    name=str(meta.get("name", "")),
                    namespace=str(meta.get("namespace") or namespace),
                    exception_type=type(exc).__name__,
                    exception_message=apply_error_message(exc),
                    is_retryable=classify_apply_error(exc),
                )
            )
    return errors
