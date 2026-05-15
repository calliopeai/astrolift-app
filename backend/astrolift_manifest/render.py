"""
NormalizedManifest → Kubernetes resource dicts.

Pure-Python renderer: takes the platform's normalized manifest plus
deployment-time inputs (image_tag, namespace, environment metadata)
and emits a list of Kubernetes resource dicts ready for
``client-go``-style apply by the workflow activity.

What this renderer covers today
-------------------------------

* ``workload.kind = deployment`` → ``apps/v1 Deployment``
  + a ``v1 Service`` per primary container with ``port > 0``.
* ``workload.kind = cronjob`` → ``batch/v1 CronJob`` (requires
  ``schedule``).
* HPA when ``hpa_min`` / ``hpa_max`` are set on a deployment workload
  → ``autoscaling/v2 HorizontalPodAutoscaler``.
* Healthchecks (``http`` / ``tcp`` / ``exec``) → ``livenessProbe``
  and ``readinessProbe`` on the primary container.
* Env tuples → ``env`` entries (literal name/value pairs).
* Resource requests + limits, replicas, image tag, command / args.

Out of scope (deliberately): StatefulSet (PVC topology), Job
(one-shot), Ingress generation (lives in the ingress driver),
per-container securityContext, volume/PVC rendering, network
policies, service mesh annotations. Those slot in as separate
``render_*`` modules without changing this one.

Determinism
-----------

Every dict is built with deterministic key ordering and the
manifest list is sorted by ``(kind, name)`` so the hash of the
rendered output is stable for the same input — important for
the apply step's diff-and-update behavior.
"""

from __future__ import annotations

from typing import Any

from astrolift_manifest.types import (
    ContainerManifest,
    NormalizedManifest,
    WorkloadManifest,
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_manifests(
    manifest: NormalizedManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    environment_name: str,
    labels: dict[str, str] | None = None,
    env_from_secret_refs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Render every workload in ``manifest`` into a list of K8s dicts.

    ``image_repository`` is the registry-side repo (e.g.
    ``ghcr.io/acme/web``); ``image_tag`` is appended with ``:`` and
    sets every container's image. Per-container image overrides
    (``image_ref`` on the container) take precedence so a sidecar can
    pin its own image.

    ``env_from_secret_refs`` is the list of k8s Secret names every
    primary container should pull ``envFrom: secretRef:`` from. The
    deploy pipeline computes this from the app's active
    ``AppSecretBundleRef`` rows + the synthesized
    ``astrolift-bindings-<app-slug>`` Secret holding managed-service
    connection envelopes. Order matters: later secrets shadow earlier
    keys on collision per k8s envFrom semantics, so bindings appear
    after operator-authored bundles.
    """
    base_labels = {
        "astrolift.dev/app": manifest.name,
        "astrolift.dev/environment": environment_name,
        **(labels or {}),
    }
    env_from = list(env_from_secret_refs or [])

    out: list[dict[str, Any]] = []
    for w in manifest.workloads:
        wl_labels = {
            **base_labels,
            "astrolift.dev/workload": w.name,
        }

        if w.kind == "deployment":
            out.append(
                _render_deployment(
                    w,
                    namespace=namespace,
                    image_tag=image_tag,
                    image_repository=image_repository,
                    labels=wl_labels,
                    env_from_secret_refs=env_from,
                )
            )
            svc = _render_service(w, namespace=namespace, labels=wl_labels)
            if svc is not None:
                out.append(svc)
            hpa = _render_hpa(w, namespace=namespace, labels=wl_labels)
            if hpa is not None:
                out.append(hpa)
        elif w.kind == "cronjob":
            out.append(
                _render_cronjob(
                    w,
                    namespace=namespace,
                    image_tag=image_tag,
                    image_repository=image_repository,
                    labels=wl_labels,
                    env_from_secret_refs=env_from,
                )
            )
        # statefulset / job land in follow-up render modules.

    return sorted(out, key=lambda d: (d.get("kind", ""), d["metadata"]["name"]))


# ---------------------------------------------------------------------------
# Workload renderers
# ---------------------------------------------------------------------------


def _render_deployment(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    selector = {"astrolift.dev/workload": w.name, "astrolift.dev/app": labels["astrolift.dev/app"]}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "replicas": int(w.replicas),
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": {**labels, **selector}},
                "spec": _pod_spec(
                    w,
                    image_tag=image_tag,
                    image_repository=image_repository,
                    env_from_secret_refs=env_from_secret_refs,
                ),
            },
        },
    }


def _render_cronjob(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    if not w.schedule:
        # Should not happen — parser rejects cronjob without schedule.
        # Keep the safety check explicit so a future bug here is loud.
        raise ValueError(f"cronjob workload {w.name!r} has no schedule")
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "schedule": w.schedule,
            "concurrencyPolicy": "Forbid",
            "jobTemplate": {
                "spec": {
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            **_pod_spec(
                                w,
                                image_tag=image_tag,
                                image_repository=image_repository,
                                env_from_secret_refs=env_from_secret_refs,
                            ),
                            "restartPolicy": "OnFailure",
                        },
                    },
                },
            },
        },
    }


def _render_service(
    w: WorkloadManifest,
    *,
    namespace: str,
    labels: dict[str, str],
) -> dict[str, Any] | None:
    primary = _primary_container(w)
    if primary is None or primary.port <= 0:
        return None
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {"astrolift.dev/workload": w.name, "astrolift.dev/app": labels["astrolift.dev/app"]},
            "ports": [
                {
                    "name": "http",
                    "port": int(primary.port),
                    "targetPort": int(primary.port),
                    "protocol": "TCP",
                },
            ],
        },
    }


def _render_hpa(
    w: WorkloadManifest,
    *,
    namespace: str,
    labels: dict[str, str],
) -> dict[str, Any] | None:
    if w.hpa_min is None and w.hpa_max is None:
        return None
    if w.hpa_min is None or w.hpa_max is None:
        # Both bounds required — render none so the operator notices.
        return None
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "scaleTargetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": w.name,
            },
            "minReplicas": int(w.hpa_min),
            "maxReplicas": int(w.hpa_max),
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": int(w.hpa_target_cpu_pct),
                        },
                    },
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Pod / container helpers
# ---------------------------------------------------------------------------


def _pod_spec(
    w: WorkloadManifest,
    *,
    image_tag: str,
    image_repository: str,
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "containers": [
            _render_container(
                c,
                workload=w,
                image_tag=image_tag,
                image_repository=image_repository,
                env_from_secret_refs=env_from_secret_refs,
            )
            for c in w.containers
        ],
    }


def _render_container(
    c: ContainerManifest,
    *,
    workload: WorkloadManifest,
    image_tag: str,
    image_repository: str,
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": c.name,
        "image": c.image_ref or f"{image_repository}:{image_tag}",
    }
    if c.port > 0:
        spec["ports"] = [{"containerPort": int(c.port), "protocol": "TCP", "name": "http"}]
    if c.command:
        spec["command"] = list(c.command)
    if c.args:
        spec["args"] = list(c.args)
    if c.env:
        spec["env"] = [{"name": name, "value": value} for name, value in c.env]
    if env_from_secret_refs:
        # Auto-inject every operator-authored SecretBundle + the
        # synthesized managed-service bindings Secret. Later refs
        # shadow earlier keys per k8s envFrom semantics, so the
        # caller orders bundles first then bindings.
        spec["envFrom"] = [{"secretRef": {"name": ref}} for ref in env_from_secret_refs]
    probe = _render_probe(c)
    if probe is not None:
        spec["livenessProbe"] = probe
        spec["readinessProbe"] = probe
    resources = _render_resources(workload)
    if resources:
        spec["resources"] = resources
    return spec


def _render_probe(c: ContainerManifest) -> dict[str, Any] | None:
    """Translate the manifest healthcheck shape to a k8s Probe.

    All probes share the same triplet (initialDelay, period, timeout)
    so the platform's defaults are visible at one site rather than
    sprinkled per-call. Production tuning lives behind workload
    overrides in a follow-up PR.
    """
    if c.healthcheck_kind == "none":
        return None
    base: dict[str, Any] = {
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "timeoutSeconds": 2,
        "successThreshold": 1,
        "failureThreshold": 3,
    }
    port = c.healthcheck_port if c.healthcheck_port is not None else c.port
    if c.healthcheck_kind == "http":
        return {
            **base,
            "httpGet": {
                "path": c.healthcheck_value or "/health",
                "port": int(port) if port else 80,
                "scheme": "HTTP",
            },
        }
    if c.healthcheck_kind == "tcp":
        return {
            **base,
            "tcpSocket": {"port": int(port) if port else 80},
        }
    if c.healthcheck_kind == "exec":
        # value is a space-delimited command; split keeps it simple.
        cmd = (c.healthcheck_value or "").split() if c.healthcheck_value else ["/bin/true"]
        return {
            **base,
            "exec": {"command": cmd},
        }
    return None


def _render_resources(w: WorkloadManifest) -> dict[str, Any]:
    """Workload-level cpu/memory shape → k8s resources spec.

    Sidecars share the workload's defaults. If/when sidecars need
    independent limits, extend ContainerManifest with cpu/memory
    fields and override here.
    """
    requests: dict[str, str] = {}
    limits: dict[str, str] = {}
    if w.cpu_request:
        requests["cpu"] = w.cpu_request
    if w.cpu_limit:
        limits["cpu"] = w.cpu_limit
    if w.memory_request:
        requests["memory"] = w.memory_request
    if w.memory_limit:
        limits["memory"] = w.memory_limit
    if not requests and not limits:
        return {}
    spec: dict[str, Any] = {}
    if requests:
        spec["requests"] = requests
    if limits:
        spec["limits"] = limits
    return spec


def _primary_container(w: WorkloadManifest) -> ContainerManifest | None:
    for c in w.containers:
        if c.is_primary:
            return c
    return w.containers[0] if w.containers else None
