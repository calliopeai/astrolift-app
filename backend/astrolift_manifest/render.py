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
* ``workload.kind = task`` → ``batch/v1 Job`` (one-shot, runs once;
  ``completions: 1`` / ``backoffLimit: 0`` / ``restartPolicy: Never``).
* ``workload.kind = agent`` → ``apps/v1 Deployment`` (same shape as
  ``deployment``) + a ``astrolift.dev/workload-kind: agent`` pod
  annotation + injected ``ASTROLIFT_*`` dispatch env vars, plus the
  matching Service and HPA.
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

# Platform → K8s ``CronJob.spec.concurrencyPolicy`` mapping (#427).
# ``forbid`` / ``queue`` / ``replace`` are the platform names; K8s
# expects PascalCase enum strings.
_CONCURRENCY_POLICY_K8S = {
    "forbid": "Forbid",
    "queue": "Allow",
    "replace": "Replace",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_manifests(
    manifest: NormalizedManifest,
    *,
    app_slug: str = "",
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
        # Use the app slug (DNS-safe) not the display name — K8s label
        # values must match ([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9].
        "astrolift.dev/app": app_slug or manifest.name,
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
        elif w.kind == "task":
            out.append(
                _render_task(
                    w,
                    namespace=namespace,
                    image_tag=image_tag,
                    image_repository=image_repository,
                    labels=wl_labels,
                    env_from_secret_refs=env_from,
                )
            )
        elif w.kind == "agent":
            # Agents are deployments with platform annotations + injected
            # dispatch env vars; they still get a Service + HPA.
            out.append(
                _render_agent(
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
        elif w.kind == "workflow":
            # Workflow workers are deployments with platform annotations +
            # injected Temporal env vars; they still get a Service + HPA.
            out.append(
                _render_workflow_worker(
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
        elif w.kind == "function":
            out.append(
                _render_function(
                    w,
                    namespace=namespace,
                    image_tag=image_tag,
                    image_repository=image_repository,
                    labels=wl_labels,
                    env_from_secret_refs=env_from,
                )
            )
        elif w.kind == "statefulset":
            # StatefulSet + headless Service for stable DNS per pod.
            # VolumeClaimTemplates are added when storage_size is set.
            sts, headless_svc = _render_statefulset(
                w,
                namespace=namespace,
                image_tag=image_tag,
                image_repository=image_repository,
                labels=wl_labels,
                env_from_secret_refs=env_from,
            )
            out.append(sts)
            out.append(headless_svc)
            # Also emit a regular Service if the workload has a port,
            # so the StatefulSet is reachable via a stable ClusterIP.
            svc = _render_service(w, namespace=namespace, labels=wl_labels)
            if svc is not None:
                out.append(svc)
        # job lands in a follow-up render module.

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
            "concurrencyPolicy": _CONCURRENCY_POLICY_K8S[w.concurrency_policy or "forbid"],
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


def _render_task(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Render a ``task`` workload into a one-shot ``batch/v1 Job``.

    Unlike the CronJob renderer there is no ``schedule`` or
    ``concurrencyPolicy`` and no ``jobTemplate`` wrapper — the pod spec
    sits directly under ``spec.template``. ``completions: 1`` /
    ``backoffLimit: 0`` make this run exactly once with no retries, and
    ``restartPolicy: Never`` surfaces a failed run as a failed Pod
    rather than a crash-looping container.
    """
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "completions": 1,
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    **_pod_spec(
                        w,
                        image_tag=image_tag,
                        image_repository=image_repository,
                        env_from_secret_refs=env_from_secret_refs,
                    ),
                    "restartPolicy": "Never",
                },
            },
        },
    }


def _render_agent(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Render an ``agent`` workload into an ``apps/v1 Deployment``.

    An agent is a long-running deployment (same K8s shape as
    ``kind = deployment`` — the caller still emits the matching Service
    and HPA) plus two platform extensions:

    * a ``astrolift.dev/workload-kind: agent`` pod-template annotation so
      the control plane can identify agent pods without re-reading the
      manifest, and
    * three ``ASTROLIFT_*`` env vars injected into the primary container
      (``ASTROLIFT_WORKLOAD_KIND`` / ``ASTROLIFT_MAX_RETRIES`` /
      ``ASTROLIFT_TOOL_TIMEOUT``) so the in-pod agent runtime reads its
      dispatch budget from the environment.

    The env vars are prepended ahead of any manifest-authored ``env:``
    so an operator who sets, say, ``ASTROLIFT_MAX_RETRIES`` explicitly on
    the container shadows the injected default (later entries win under
    k8s env semantics).
    """
    dep = _render_deployment(
        w,
        namespace=namespace,
        image_tag=image_tag,
        image_repository=image_repository,
        labels=labels,
        env_from_secret_refs=env_from_secret_refs,
    )
    template = dep["spec"]["template"]
    template["metadata"]["annotations"] = {"astrolift.dev/workload-kind": "agent"}

    injected = [
        {"name": "ASTROLIFT_WORKLOAD_KIND", "value": "agent"},
        {"name": "ASTROLIFT_MAX_RETRIES", "value": str(int(w.max_retries))},
        {"name": "ASTROLIFT_TOOL_TIMEOUT", "value": str(int(w.tool_timeout_seconds))},
    ]
    primary = _primary_container(w)
    pod_containers = template["spec"]["containers"]
    target = next((c for c in pod_containers if primary is not None and c["name"] == primary.name), None)
    if target is None and pod_containers:
        target = pod_containers[0]
    if target is not None:
        target["env"] = injected + target.get("env", [])
    return dep


def _render_workflow_worker(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Render a ``workflow`` workload into an ``apps/v1 Deployment``.

    A workflow worker is a long-running deployment (same K8s shape as
    ``kind = deployment``) that registers Temporal workflow + activity
    functions against the platform Temporal cluster. On top of the base
    deployment it adds two platform extensions:

    * a ``astrolift.dev/workload-kind: workflow`` pod-template annotation
      so the control plane can identify worker pods without re-reading the
      manifest, and
    * three ``ASTROLIFT_*`` / ``TEMPORAL_*`` env vars injected into the
      primary container (``ASTROLIFT_WORKFLOW_TYPE`` / ``ASTROLIFT_TASK_QUEUE``
      / ``TEMPORAL_NAMESPACE``) so the in-pod worker reads which workflow
      type to register, which task queue to poll, and which Temporal
      namespace to connect to.

    The env vars are prepended ahead of any manifest-authored ``env:`` so
    an operator who sets, say, ``TEMPORAL_NAMESPACE`` explicitly on the
    container shadows the injected value (later entries win under k8s env
    semantics). The poller concurrency caps
    (``max_concurrent_activities`` / ``max_concurrent_workflows``) tune the
    worker but are not rendered as K8s output — the worker reads them from
    the platform Temporal config — so they intentionally do not appear in
    the pod spec.

    Like ``kind = deployment``, the caller still emits the matching
    Service (when the primary container exposes a port) and HPA.
    """
    dep = _render_deployment(
        w,
        namespace=namespace,
        image_tag=image_tag,
        image_repository=image_repository,
        labels=labels,
        env_from_secret_refs=env_from_secret_refs,
    )
    template = dep["spec"]["template"]
    template["metadata"]["annotations"] = {"astrolift.dev/workload-kind": "workflow"}

    injected = [
        {"name": "ASTROLIFT_WORKFLOW_TYPE", "value": w.workflow_type},
        {"name": "ASTROLIFT_TASK_QUEUE", "value": w.task_queue},
        {"name": "TEMPORAL_NAMESPACE", "value": w.temporal_namespace or "default"},
    ]
    primary = _primary_container(w)
    pod_containers = template["spec"]["containers"]
    target = next((c for c in pod_containers if primary is not None and c["name"] == primary.name), None)
    if target is None and pod_containers:
        target = pod_containers[0]
    if target is not None:
        target["env"] = injected + target.get("env", [])
    return dep


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


def _render_function(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Render a ``kind: function`` workload as a Knative Service.

    Requires Knative Serving on the tenant cluster. The Service replaces
    both the Deployment and HPA — Knative's autoscaler owns replica count
    and implements scale-to-zero via ``min_scale = 0``.
    """
    primary = _primary_container(w)
    if primary is None:
        raise ValueError(f"function workload {w.name!r} has no containers")

    image = (
        primary.image_ref
        if primary.image_ref
        else f"{image_repository}:{image_tag}"
    )
    container_spec: dict[str, Any] = {
        "image": image,
        "resources": _resource_spec(w),
    }
    if primary.port > 0:
        container_spec["ports"] = [{"containerPort": primary.port, "name": "http1"}]
    if primary.command:
        container_spec["command"] = list(primary.command)
    if primary.args:
        container_spec["args"] = list(primary.args)

    env = list(primary.env)
    if env_from_secret_refs:
        container_spec["envFrom"] = [
            {"secretRef": {"name": ref}} for ref in env_from_secret_refs
        ]
    if env:
        container_spec["env"] = [{"name": k, "value": v} for k, v in env]

    return {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
            "annotations": {
                "autoscaling.knative.dev/minScale": str(w.min_scale),
                "autoscaling.knative.dev/maxScale": str(w.max_scale),
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "labels": {
                        **labels,
                        "astrolift.dev/workload-kind": "function",
                    },
                    "annotations": {
                        "autoscaling.knative.dev/minScale": str(w.min_scale),
                        "autoscaling.knative.dev/maxScale": str(w.max_scale),
                    },
                },
                "spec": {
                    "containerConcurrency": w.function_concurrency,
                    "timeoutSeconds": w.function_timeout_seconds,
                    "containers": [container_spec],
                },
            }
        },
    }


def _render_statefulset(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Render a ``kind: statefulset`` workload.

    Returns a 2-tuple: (StatefulSet, headless Service). The headless
    Service (``clusterIP: None``) gives each pod a stable DNS name:
    ``<pod-name>.<svc-name>.<namespace>.svc.cluster.local``.

    When ``storage_size`` is set, a ``volumeClaimTemplate`` is emitted
    so each pod gets its own persistent volume.
    """
    selector = {
        "astrolift.dev/workload": w.name,
        "astrolift.dev/app": labels["astrolift.dev/app"],
    }
    headless_svc_name = f"{w.name}-headless"

    pod_spec = _pod_spec(
        w,
        image_tag=image_tag,
        image_repository=image_repository,
        env_from_secret_refs=env_from_secret_refs,
    )

    volume_claim_templates: list[dict[str, Any]] = []
    if w.storage_size:
        volume_claim_templates.append(
            {
                "metadata": {"name": f"{w.name}-data"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": w.storage_size}},
                    **({"storageClassName": w.storage_class} if w.storage_class else {}),
                },
            }
        )

    sts: dict[str, Any] = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "serviceName": headless_svc_name,
            "replicas": int(w.replicas),
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": {**labels, **selector}},
                "spec": pod_spec,
            },
        },
    }
    if volume_claim_templates:
        sts["spec"]["volumeClaimTemplates"] = volume_claim_templates

    # Headless Service — required for stable pod DNS in StatefulSet.
    headless: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": headless_svc_name,
            "namespace": namespace,
            "labels": {**labels, "astrolift.dev/headless": "true"},
        },
        "spec": {
            "clusterIP": "None",
            "selector": selector,
            "ports": [],
        },
    }
    # Populate headless service ports from the primary container.
    primary = _primary_container(w)
    if primary and primary.port > 0:
        headless["spec"]["ports"] = [
            {"name": "app", "port": primary.port, "targetPort": primary.port}
        ]

    return sts, headless
