"""Sandbox baseline shared by agent task Jobs and agent box Jobs (#1848).

Agent pods run untrusted, model-driven code. Without bounds a runaway agent
can starve its node, and with the namespace default ServiceAccount token
mounted it can call the Kubernetes API.

The baseline stops short of ``runAsNonRoot`` and dropping capabilities:
every astrolift-agents image runs as root and the runner ``apt-get
install``s the manifest's roster, which needs root's default capabilities.
IRSA and pod-identity webhooks project their own token volume, so turning
off the default token mount leaves managed-model credentials intact.
"""

from __future__ import annotations

from django.conf import settings


def harden_agent_pod(pod_spec: dict) -> dict:
    """Apply the agent sandbox baseline to a pod spec in place."""
    pod_spec["automountServiceAccountToken"] = False
    pod_spec.setdefault("securityContext", {})["seccompProfile"] = {"type": "RuntimeDefault"}
    for container in pod_spec.get("containers") or []:
        container.setdefault("securityContext", {})["allowPrivilegeEscalation"] = False
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
