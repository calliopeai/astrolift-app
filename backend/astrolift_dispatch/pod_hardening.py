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

#: uid/gid of the ``agent`` user in every astrolift-agents image.
AGENT_UID = 42042

NON_ROOT_INSTALL_CONFLICT = (
    "this environment spec runs as non-root, which cannot install packages at boot; "
    "turn off allow_install and bake the packages into the image, or run the spec as root"
)


def harden_agent_pod(pod_spec: dict, *, non_root: bool = False) -> dict:
    """Apply the agent sandbox baseline to a pod spec in place."""
    pod_spec["automountServiceAccountToken"] = False
    pod_security = pod_spec.setdefault("securityContext", {})
    pod_security["seccompProfile"] = {"type": "RuntimeDefault"}
    if non_root:
        pod_security.update(
            {
                "runAsNonRoot": True,
                "runAsUser": AGENT_UID,
                "runAsGroup": AGENT_UID,
                "fsGroup": AGENT_UID,
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
    return pod_spec
