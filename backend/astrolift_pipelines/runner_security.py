"""
Runner sandbox security helpers (#91).

Three concerns:

1. ``validate_runner_image_policy`` — blocks container images from
   registries not on the operator's allowlist. Called before the runner
   agent starts a container job.

2. ``render_job_pod_security_context`` — returns a K8s-compatible
   securityContext dict for rootless job containers (runAsNonRoot,
   no privilege escalation, read-only root not required at v1).

3. ``per_job_resource_limits`` — returns Docker/K8s resource limit
   dicts derived from the org's runner settings (or platform defaults).

Design note
-----------
The sandbox mode is intentionally a pluggable strategy: callers receive
dicts rather than CLI strings so the same data can drive both Docker
``docker run`` flags and K8s pod specs without re-deriving values at the
call site. v2 VM-level isolation (Firecracker, Apple Virtualization.framework)
will add a third strategy without touching this module's interface.

Reference: issue #91.
"""

from __future__ import annotations

import logging

log = logging.getLogger("astrolift_pipelines.runner_security")

# Default per-job resource limits applied when the org hasn't configured
# custom values. Operators may lower these per runner; pipeline authors
# cannot raise them (the agent enforces this floor-and-ceiling contract).
_DEFAULT_CPU = "2"  # --cpus for Docker / cpu limit for K8s
_DEFAULT_MEMORY = "4g"  # --memory for Docker
_DEFAULT_MEMORY_K8S = "4Gi"  # requests.memory / limits.memory for K8s pods
_DEFAULT_PIDS_LIMIT = 512  # --pids-limit (prevents fork bombs)

# The rootless runner process user. Nobody (65534) is conventional for
# this purpose; it has no home directory and no shell.
_ROOTLESS_UID = 65534
_ROOTLESS_GID = 65534


def validate_runner_image_policy(image: str, org) -> bool:
    """Return True if ``image`` is allowed under the org's image policy.

    An org may configure an ``allowed_runner_registries`` list in its
    settings (a JSON array of registry host strings such as
    ``["ghcr.io", "docker.io", "quay.io"]``). When the list is present
    and non-empty this function returns True only if the image's
    registry host is in the list.

    When the allowlist is absent or empty all registries are permitted
    (open policy — typical for self-hosted installs where the operator
    controls the runner machines).

    The ``image`` argument may be:
    - ``ubuntu:22.04``           → implicit docker.io/library registry
    - ``ghcr.io/myorg/image:v1`` → explicit ghcr.io registry
    - ``registry.local:5000/img``→ private registry with port

    Args:
        image: Container image reference as a string.
        org:   Organization model instance. The function reads
               ``org.runner_settings`` (a JSONField dict) if present.

    Returns:
        True if the image is allowed, False if blocked.
    """
    if not image:
        log.warning("validate_runner_image_policy: empty image reference — blocked")
        return False

    # Resolve the registry host from the image string.
    registry_host = _parse_registry_host(image)

    # Read per-org allowlist from runner_settings if the field exists.
    runner_settings: dict = {}
    if hasattr(org, "runner_settings") and isinstance(org.runner_settings, dict):
        runner_settings = org.runner_settings

    allowlist: list[str] = runner_settings.get("allowed_runner_registries", [])

    if not allowlist:
        # Open policy — no restriction configured.
        return True

    allowed = registry_host in allowlist
    if not allowed:
        log.warning(
            "validate_runner_image_policy: registry blocked",
            extra={"registry": registry_host, "org_slug": getattr(org, "slug", "?")},
        )
    return allowed


def _parse_registry_host(image: str) -> str:
    """Extract the registry hostname from an image reference.

    Docker image refs don't carry a scheme, so we normalise by adding
    ``//`` before parsing.
    """
    # Split on the first slash. If the part before the first slash
    # contains a dot or colon it's a registry host; otherwise it's
    # the library short-name form (implicit docker.io).
    parts = image.split("/", 1)
    first = parts[0]
    if "." in first or ":" in first:
        # Strip tag and digest from the host+port part.
        return first.split(":")[0] if ":" in first and "/" not in first else first.split(":")[0]
    # Implicit docker.io (e.g. "ubuntu:22.04", "library/ubuntu")
    return "docker.io"


def render_job_pod_security_context() -> dict:
    """Return a K8s-compatible securityContext for rootless job pods.

    The returned dict maps directly to a Kubernetes
    ``Pod.spec.containers[*].securityContext`` or
    ``Pod.spec.securityContext`` block. It enforces:

    - ``runAsNonRoot: true`` — kernel rejects root UIDs at container start
    - ``runAsUser`` / ``runAsGroup`` — nobody (65534)
    - ``allowPrivilegeEscalation: false`` — no setuid / file capabilities
    - ``readOnlyRootFilesystem: false`` — steps need a writable workspace
      (the workspace is a bind-mounted temp dir, not the root FS)
    - ``seccompProfile`` — RuntimeDefault (least privilege without a
      custom profile)
    - ``capabilities.drop: ["ALL"]`` — no Linux capabilities granted

    Returns:
        dict compatible with ``kubernetes.client.V1SecurityContext``.
    """
    return {
        "runAsNonRoot": True,
        "runAsUser": _ROOTLESS_UID,
        "runAsGroup": _ROOTLESS_GID,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": False,
        "seccompProfile": {"type": "RuntimeDefault"},
        "capabilities": {"drop": ["ALL"]},
    }


def per_job_resource_limits(org) -> dict:
    """Return resource limit dicts for a job pod / container.

    Reads org-level overrides from ``org.runner_settings`` and falls
    back to platform defaults. Returns two sub-dicts:

    ``docker``
        Arguments suitable for ``docker run`` (string values, Docker CLI
        format).

    ``kubernetes``
        Arguments suitable for a K8s ``resources`` block (string values,
        K8s quantity format).

    Operators may lower defaults per-runner. Pipeline authors cannot
    raise them — the agent enforces the ceiling at job claim time.

    Args:
        org: Organization model instance.

    Returns:
        {
            "docker": {
                "cpus": "2",
                "memory": "4g",
                "pids_limit": 512,
            },
            "kubernetes": {
                "requests": {"cpu": "500m", "memory": "512Mi"},
                "limits": {"cpu": "2", "memory": "4Gi"},
            }
        }
    """
    runner_settings: dict = {}
    if hasattr(org, "runner_settings") and isinstance(org.runner_settings, dict):
        runner_settings = org.runner_settings

    cpu = str(runner_settings.get("job_cpu_limit", _DEFAULT_CPU))
    memory_docker = str(runner_settings.get("job_memory_limit", _DEFAULT_MEMORY))
    memory_k8s = str(runner_settings.get("job_memory_limit_k8s", _DEFAULT_MEMORY_K8S))
    pids_limit = int(runner_settings.get("job_pids_limit", _DEFAULT_PIDS_LIMIT))

    return {
        "docker": {
            "cpus": cpu,
            "memory": memory_docker,
            "pids_limit": pids_limit,
        },
        "kubernetes": {
            "requests": {
                "cpu": "500m",
                "memory": "512Mi",
            },
            "limits": {
                "cpu": cpu,
                "memory": memory_k8s,
            },
        },
    }
