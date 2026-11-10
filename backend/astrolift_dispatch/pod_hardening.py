"""Sandbox baseline shared by agent task Jobs and agent box Jobs (#1848).

Agent pods run untrusted, model-driven code. Without bounds a runaway agent
can starve its node, and with the namespace default ServiceAccount token
mounted it can call the Kubernetes API.

Two modes (#1855). Root, the default, stops short of ``runAsNonRoot`` and
dropping capabilities because the runner ``apt-get install``s the
manifest's roster, which needs root's default capabilities. Non-root runs as
the images' ``agent`` user with every capability dropped, so nothing can be
installed at boot. IRSA and pod-identity webhooks project their own token
volume, so turning off the default token mount leaves managed-model
credentials intact in both modes.
"""

from __future__ import annotations

from django.conf import settings

#: uid/gid of the ``agent`` user in every astrolift-agents image; the
#: default for the ``AGENT_POD_UID`` / ``AGENT_POD_GID`` Constance settings.
AGENT_UID = 42042

NON_ROOT_INSTALL_CONFLICT = (
    "this environment spec runs as non-root, which cannot install packages at boot; "
    "turn off allow_install and bake the packages into the image, or run the spec as root"
)


def harden_agent_pod(pod_spec: dict, *, non_root: bool = False, spec=None) -> dict:
    """Apply the agent sandbox baseline to a pod spec in place.

    ``spec`` (an AgentEnvironmentSpec) that asks for GPUs gets them on the
    first container, the agent, plus the GPU-pool tolerations and type
    affinity a manifest workload gets (#2039).
    """
    runtime_class = str(settings.AGENT_RUNTIME_CLASS or "").strip()
    validate_agent_runtime(runtime_class)
    if runtime_class:
        pod_spec["runtimeClassName"] = runtime_class
    pod_spec["automountServiceAccountToken"] = False
    pod_security = pod_spec.setdefault("securityContext", {})
    pod_security["seccompProfile"] = {"type": "RuntimeDefault"}
    if non_root:
        from constance import config

        gid = int(config.AGENT_POD_GID)
        pod_security.update(
            {
                "runAsNonRoot": True,
                "runAsUser": int(config.AGENT_POD_UID),
                "runAsGroup": gid,
                "fsGroup": gid,
            }
        )
    for container in pod_spec.get("containers") or []:
        container_security = container.setdefault("securityContext", {})
        container_security["allowPrivilegeEscalation"] = False
        if non_root:
            container_security["capabilities"] = {"drop": ["ALL"]}
        container["resources"] = {
            "requests": {
                "cpu": settings.AGENT_POD_CPU_REQUEST,
                "memory": settings.AGENT_POD_MEMORY_REQUEST,
            },
            "limits": {
                "cpu": settings.AGENT_POD_CPU_LIMIT,
                "memory": settings.AGENT_POD_MEMORY_LIMIT,
            },
        }
    if getattr(spec, "gpu", 0) and pod_spec.get("containers"):
        from astrolift_manifest.render import gpu_resource_name, gpu_scheduling

        pod_spec["containers"][0]["resources"]["limits"][gpu_resource_name(spec)] = str(spec.gpu)
        scheduling = gpu_scheduling(spec)
        pod_spec.setdefault("tolerations", []).extend(scheduling["tolerations"])
        if "affinity" in scheduling:
            pod_spec["affinity"] = scheduling["affinity"]
    apply_nitro_scheduling(pod_spec)
    return pod_spec


def apply_nitro_scheduling(pod_spec: dict) -> None:
    """Permit only selected Nitro runtimes onto the dedicated sandbox pool."""
    if pod_spec.get("runtimeClassName") not in {"isolated-sandbox", "confidential-sandbox"}:
        return
    toleration = {
        "key": "astrolift.dev/nitro-only",
        "operator": "Equal",
        "value": "true",
        "effect": "NoSchedule",
    }
    tolerations = pod_spec.setdefault("tolerations", [])
    if toleration not in tolerations:
        tolerations.append(toleration)


class AgentRuntimeClassError(RuntimeError):
    pass


def validate_agent_runtime(runtime_class: str | None) -> None:
    if runtime_class == "confidential-sandbox":
        raise AgentRuntimeClassError(
            "Confidential agent tasks require final admitted per-run policy generation and attested "
            "secret delivery; AGENT_RUNTIME_CLASS alone cannot enable confidential tasks"
        )


def preflight_agent_runtime(cluster, pod_spec: dict, *, allow_confidential: bool = False) -> None:
    runtime_class = pod_spec.get("runtimeClassName")
    if not allow_confidential:
        validate_agent_runtime(runtime_class)
    if not runtime_class:
        return
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    try:
        manifest = _driver_for_cluster(cluster).get_manifest(
            _context_for_cluster(cluster).slug, None, "RuntimeClass", runtime_class
        )
    except Exception as exc:
        raise AgentRuntimeClassError(
            f"Cannot verify agent RuntimeClass {runtime_class!r}; install it on the target cluster "
            "and allow the control plane to read RuntimeClasses, or clear AGENT_RUNTIME_CLASS."
        ) from exc
    metadata = manifest.get("metadata") if isinstance(manifest, dict) else None
    if not manifest or not isinstance(metadata, dict) or metadata.get("deletionTimestamp"):
        raise AgentRuntimeClassError(
            f"Agent RuntimeClass {runtime_class!r} is not available on the target cluster; "
            "install it before dispatch, or clear AGENT_RUNTIME_CLASS."
        )
    if runtime_class in {"isolated-sandbox", "confidential-sandbox"} and (
        manifest.get("handler") != runtime_class or metadata.get("name") != runtime_class
    ):
        raise AgentRuntimeClassError("Agent Nitro Sandbox RuntimeClass does not use the expected AWS handler")
