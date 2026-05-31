"""Runner sandbox security — rootless containers, resource limits, image policy (#91)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Images that are always allowed regardless of org policy
_PLATFORM_ALLOWED_IMAGES = {
    "ghcr.io/calliopeai/astrolift-builder",
}


def validate_runner_image_policy(image: str, org) -> bool:
    """Return True if the image is allowed under the org's runner image policy.

    The org can configure an allowlist of approved base images or prefixes.
    If no policy is set, all images are allowed (open policy).
    Platform images are always allowed.
    """
    # Strip tag/digest
    image_name = image.split(":")[0].split("@")[0]

    # Platform images always allowed
    if any(image_name == allowed or image_name.startswith(allowed + ":") for allowed in _PLATFORM_ALLOWED_IMAGES):
        return True

    extra = getattr(org, "extra_data", None) or {}
    allowlist = extra.get("runner_image_allowlist", [])

    if not allowlist:
        return True  # No policy → open

    return any(
        image_name == pattern or image_name.startswith(pattern.rstrip("*"))
        for pattern in allowlist
    )


def render_job_pod_security_context() -> dict[str, Any]:
    """Return a K8s securityContext for rootless pipeline job containers.

    runAsNonRoot prevents root-running containers. allowPrivilegeEscalation=false
    blocks setuid/setgid binaries. These are the baseline controls — operators
    can tighten further via OPA/Kyverno policies.
    """
    return {
        "runAsNonRoot": True,
        "runAsUser": 65534,       # nobody
        "runAsGroup": 65534,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": False,  # Jobs need to write temp files
        "seccompProfile": {"type": "RuntimeDefault"},
        "capabilities": {"drop": ["ALL"]},
    }


def per_job_resource_limits(org) -> dict[str, str]:
    """Return default resource limits for a pipeline job pod.

    Operators can override via org.extra_data.pipeline_job_cpu_limit etc.
    """
    extra = getattr(org, "extra_data", None) or {}
    return {
        "cpu_request": extra.get("pipeline_job_cpu_request", "100m"),
        "cpu_limit": extra.get("pipeline_job_cpu_limit", "2"),
        "memory_request": extra.get("pipeline_job_memory_request", "128Mi"),
        "memory_limit": extra.get("pipeline_job_memory_limit", "2Gi"),
    }
