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
* ``workload.kind = agent`` with ``run_family = service`` → ``apps/v1
  Deployment`` (same shape as ``deployment``) + a
  ``astrolift.dev/workload-kind: agent`` pod annotation + injected
  ``ASTROLIFT_*`` dispatch env vars, plus the matching Service and HPA.
  A ``task``-family agent (the default) renders nothing — it is
  dispatched as a one-shot K8s Job by the agent dispatch path.
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
    image_digest: str = "",
    environment_name: str,
    labels: dict[str, str] | None = None,
    env_from_secret_refs: list[str] | None = None,
    workload_env_from_secret_refs: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Render every workload in ``manifest`` into a list of K8s dicts.

    ``image_repository`` is the registry-side repo (e.g.
    ``ghcr.io/acme/web``); ``image_tag`` is appended with ``:`` and
    sets every container's image. Per-container image overrides
    (``image_ref`` on the container) take precedence so a sidecar can
    pin its own image.

    ``image_digest`` is the ``sha256:...`` the build recorded for that
    tag. When present the platform-built containers are emitted as
    ``<repo>@<digest>`` instead of ``<repo>:<tag>`` (spec 12 §9), so a
    re-pushed tag cannot change the bytes a rollback restores. Empty
    (dry-run previews, pre-build renders) keeps the tag form.

    ``env_from_secret_refs`` is the list of k8s Secret names every
    primary container should pull ``envFrom: secretRef:`` from. The
    deploy pipeline computes this from the app's active
    ``AppSecretBundleRef`` rows + the synthesized
    ``astrolift-bindings-<app-slug>`` Secret holding managed-service
    connection envelopes. Order matters: later secrets shadow earlier
    keys on collision per k8s envFrom semantics, so bindings appear
    after operator-authored bundles.

    ``workload_env_from_secret_refs`` adds selector-scoped secrets to one
    workload only. It is how a manifest can bind a shared service to ``api``
    without leaking the same credentials into ``worker``.
    """
    base_labels = {
        # Use the app slug (DNS-safe) not the display name — K8s label
        # values must match ([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9].
        "astrolift.dev/app": app_slug or manifest.name,
        "astrolift.dev/environment": environment_name,
        **(labels or {}),
    }
    common_env_from = list(env_from_secret_refs or [])
    workload_env_from = workload_env_from_secret_refs or {}

    out: list[dict[str, Any]] = []
    for w in manifest.workloads:
        env_from = [*common_env_from, *workload_env_from.get(w.name, [])]
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
                    image_digest=image_digest,
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
            monitor = _render_pod_monitor(w, namespace=namespace, labels=wl_labels)
            if monitor is not None:
                out.append(monitor)
        elif w.kind == "cronjob":
            out.append(
                _render_cronjob(
                    w,
                    namespace=namespace,
                    image_tag=image_tag,
                    image_repository=image_repository,
                    image_digest=image_digest,
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
                    image_digest=image_digest,
                    labels=wl_labels,
                    env_from_secret_refs=env_from,
                )
            )
        elif w.kind == "agent":
            # Only a service-family agent is a standing Deployment (with
            # platform annotations + injected dispatch env vars, plus its
            # Service + HPA). A task-family agent is dispatched as a one-shot
            # K8s Job by the agent dispatch path, so the renderer emits
            # nothing for it — rendering an always-on Deployment would
            # CrashLoop a run-to-completion agent (#1027).
            if w.run_family == "service":
                out.append(
                    _render_agent(
                        w,
                        namespace=namespace,
                        image_tag=image_tag,
                        image_repository=image_repository,
                        image_digest=image_digest,
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
                    image_digest=image_digest,
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
                    image_digest=image_digest,
                    labels=wl_labels,
                    env_from_secret_refs=env_from,
                )
            )
        elif w.kind == "statefulset":
            # StatefulSet + headless Service for stable DNS per pod.
            # VolumeClaimTemplates are added when the workload declares a
            # size, via storage_size or a [[workloads.volumes]] pvc entry.
            sts, headless_svc = _render_statefulset(
                w,
                namespace=namespace,
                image_tag=image_tag,
                image_repository=image_repository,
                image_digest=image_digest,
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
        elif w.kind == "static_site":
            # No K8s resource — reachability is the CNAME -> CloudFront written
            # by the deploy flow; bucket+CDN are managed services.
            continue
        elif w.kind == "faas":
            # No K8s resource — the cloud runs the function (#987). The Lambda,
            # its Function URL, and the fronting CloudFront (cdn) are managed
            # services provisioned by the faas deploy activities, not pods.
            continue
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
    image_digest: str,
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
                "metadata": {
                    "labels": {**labels, **selector},
                    **_metrics_annotations(w),
                },
                "spec": _pod_spec(
                    w,
                    image_tag=image_tag,
                    image_repository=image_repository,
                    image_digest=image_digest,
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
    image_digest: str,
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
                                image_digest=image_digest,
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
    image_digest: str,
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
                        image_digest=image_digest,
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
    image_digest: str,
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
        image_digest=image_digest,
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
    image_digest: str,
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
        image_digest=image_digest,
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


def _metrics_annotations(w: Any) -> dict[str, Any]:
    """Prometheus discovery annotations for a scrape-enabled workload (#1226).

    Returned as a mapping to splat, so a workload that opted out adds no
    ``annotations`` key at all rather than an empty one — an empty
    annotations block is a diff against every existing rendered pod.

    These are for annotation-based scrapers. The ``PodMonitor`` below is
    what the platform's own kube-prometheus-stack actually reads; both are
    emitted because an operator's cluster may run either.
    """
    if not w.metrics_enabled or not w.metrics_port:
        return {}
    return {
        "annotations": {
            "prometheus.io/scrape": "true",
            "prometheus.io/port": str(w.metrics_port),
            "prometheus.io/path": w.metrics_path or "/metrics",
        }
    }


def _render_pod_monitor(w: Any, *, namespace: str, labels: dict[str, str]) -> dict[str, Any] | None:
    """A PodMonitor for a scrape-enabled workload, or ``None``.

    A PodMonitor rather than a ServiceMonitor: a ServiceMonitor needs a
    Service, and only ``is_public`` workloads get one — a private worker
    exposing ``/metrics`` is exactly the case this is for.

    The cluster's Prometheus watches both kinds across all namespaces
    (``podMonitorSelectorNilUsesHelmValues: false``), so no label on this
    object has to match a selector the cluster sets; it is picked up by
    existing.
    """
    if not w.metrics_enabled or not w.metrics_port:
        return None
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PodMonitor",
        "metadata": {
            "name": w.name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {
                "matchLabels": {
                    "astrolift.dev/workload": w.name,
                    "astrolift.dev/app": labels["astrolift.dev/app"],
                }
            },
            # Scoped to this namespace. Omitting it lets a PodMonitor
            # select pods in other namespaces, which on a shared cluster
            # is one app scraping another.
            "namespaceSelector": {"matchNames": [namespace]},
            "podMetricsEndpoints": [
                {
                    # Addressed by number, not name. A container declares
                    # one named port, "http", for the port it serves on;
                    # a metrics port is frequently a different one and has
                    # no name to reference. prometheus-operator marks
                    # `targetPort` deprecated in favour of a named `port`,
                    # so naming the metrics container port is the tidier
                    # follow-up — it just costs a change to the container
                    # renderer's signature that this does not need.
                    "targetPort": int(w.metrics_port),
                    "path": w.metrics_path or "/metrics",
                }
            ],
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


def gpu_resource_name(w: WorkloadManifest) -> str:
    """The extended resource a GPU workload requests (#2038)."""
    return f"nvidia.com/mig-{w.mig_profile}" if w.mig_profile else "nvidia.com/gpu"


# Taints that keep everything but GPU work off GPU nodes (#2039). GKE puts
# ``nvidia.com/gpu=present:NoSchedule`` on GPU pools itself and the AKS / EKS
# GPU guides use the same key; ``astrolift.io/gpu`` is the platform's own, for
# pools an operator taints by hand. Only a workload that requests a GPU
# tolerates them, so CPU workloads and platform pods never land there.
GPU_POOL_TAINT_KEYS = ("nvidia.com/gpu", "astrolift.io/gpu")


def gpu_scheduling(w: WorkloadManifest) -> dict[str, Any]:
    """Pod-spec scheduling fields for a GPU workload: tolerations for the
    GPU-pool taints (#2039) and, when a GPU type is pinned, node affinity on
    it (#2038). Empty for a workload without GPUs."""
    if not w.gpu:
        return {}
    out: dict[str, Any] = {
        "tolerations": [
            {"key": key, "operator": "Exists", "effect": "NoSchedule"} for key in GPU_POOL_TAINT_KEYS
        ],
    }
    if w.gpu_type:
        terms = [
            {"matchExpressions": [{"key": key, "operator": "In", "values": [w.gpu_type]}]}
            for key in ("nvidia.com/gpu.product", "cloud.google.com/gke-accelerator")
        ]
        out["affinity"] = {
            "nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": terms}}
        }
    return out


def _pod_spec(
    w: WorkloadManifest,
    *,
    image_tag: str,
    image_repository: str,
    image_digest: str,
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **gpu_scheduling(w),
        "containers": [
            _render_container(
                c,
                workload=w,
                image_tag=image_tag,
                image_repository=image_repository,
                image_digest=image_digest,
                env_from_secret_refs=env_from_secret_refs,
            )
            for c in w.containers
        ],
    }


def _platform_image(*, image_repository: str, image_tag: str, image_digest: str) -> str:
    """The image string for a container the platform builds.

    Prefers the digest: a tag is mutable, so re-pushing it changes the
    bytes a "rollback to that release" actually ships. ``build_image``
    records the digest best-effort (a registry read can fail on an
    otherwise-good build), so fall back to the tag when it is absent
    rather than blocking the deploy.
    """
    ref = f"{image_repository}:{image_tag}"
    if not image_digest:
        return ref
    from astrolift_workflows.activities.image_digest import pin_to_digest  # noqa: PLC0415

    return str(pin_to_digest(ref=ref, digest=image_digest))


def _render_container(
    c: ContainerManifest,
    *,
    workload: WorkloadManifest,
    image_tag: str,
    image_repository: str,
    image_digest: str,
    env_from_secret_refs: list[str] | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": c.name,
        "image": c.image_ref
        or _platform_image(image_repository=image_repository, image_tag=image_tag, image_digest=image_digest),
    }
    if c.port > 0:
        spec["ports"] = [{"containerPort": int(c.port), "protocol": "TCP", "name": "http"}]
    if c.command:
        spec["command"] = list(c.command)
    if c.args:
        spec["args"] = list(c.args)
    # The commit the container is running, from the tag the platform deploys
    # (#1709). The tag IS the commit SHA, and nothing put it anywhere the
    # process could read it -- so an app had no way to report its own version
    # and the only answer available was the tag on the workload.
    #
    # That distinction matters for confirming a rollout: the tag can be right
    # while the pod is stale (a StatefulSet whose pod never rolled, for
    # instance), so the honest check is what the running container says about
    # itself, not what the spec says about it.
    #
    # Injected before the manifest's own env so an app that already sets either
    # name keeps its value -- later entries win in the list this builds, and
    # this is a default, not an override.
    version_env = [
        {"name": key, "value": image_tag}
        for key in ("ASTROLIFT_COMMIT", "GIT_SHA")
        if image_tag and key not in {name for name, _ in c.env}
    ]
    if c.env or version_env:
        spec["env"] = version_env + [{"name": name, "value": value} for name, value in c.env]
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
    if workload.gpu and c is _primary_container(workload):
        # GPUs go on the primary container only; a sidecar asking for one
        # would hold a device it never uses (#2038).
        resources.setdefault("limits", {})[gpu_resource_name(workload)] = str(workload.gpu)
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
        # No http probe without a real port to hit. A port-less container
        # would otherwise get an httpGet on a fabricated port and CrashLoop
        # on a probe that can never succeed (#1033).
        if not port:
            return None
        return {
            **base,
            "httpGet": {
                "path": c.healthcheck_value or "/health",
                "port": int(port),
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
    image_digest: str,
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

    image = primary.image_ref or _platform_image(
        image_repository=image_repository, image_tag=image_tag, image_digest=image_digest
    )
    container_spec: dict[str, Any] = {
        "image": image,
        "resources": _render_resources(w),
    }
    if primary.port > 0:
        container_spec["ports"] = [{"containerPort": primary.port, "name": "http1"}]
    if primary.command:
        container_spec["command"] = list(primary.command)
    if primary.args:
        container_spec["args"] = list(primary.args)

    env = list(primary.env)
    if env_from_secret_refs:
        container_spec["envFrom"] = [{"secretRef": {"name": ref}} for ref in env_from_secret_refs]
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


# Pod ``securityContext.fsGroup`` applied to a workload that mounts a pvc
# when neither ``fs_group`` nor ``security.run_as_user`` says otherwise
# (#1722).
#
# A PVC arrives root-owned. A container running as any non-root uid cannot
# write to it, which surfaces as the app dying on its own data directory.
# fsGroup fixes that for EVERY image without the app declaring its uid,
# because the kubelet both chowns the volume to this GID with g+rwx and adds
# the GID to the container's supplementary groups -- so the write succeeds
# whatever uid the image runs as. That property is what lets this be a
# default instead of required configuration.
#
# Emitted only for workloads that actually mount a pvc: an unmounted
# workload gains nothing from it, and a blanket fsGroup would relabel
# every other volume kind a pod carries.
DEFAULT_PVC_FS_GROUP = 1000


def _render_statefulset(
    w: WorkloadManifest,
    *,
    namespace: str,
    image_tag: str,
    image_repository: str,
    image_digest: str,
    labels: dict[str, str],
    env_from_secret_refs: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Render a ``kind: statefulset`` workload.

    Returns a 2-tuple: (StatefulSet, headless Service). The headless
    Service (``clusterIP: None``) gives each pod a stable DNS name:
    ``<pod-name>.<svc-name>.<namespace>.svc.cluster.local``.

    When the workload declares a size -- via ``storage_size`` or a
    ``[[workloads.volumes]]`` pvc entry -- a ``volumeClaimTemplate`` is
    emitted so each pod gets its own persistent volume.
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
        image_digest=image_digest,
        env_from_secret_refs=env_from_secret_refs,
    )

    volume_claim_templates: list[dict[str, Any]] = []
    # The claim is described in either of two places and they have to agree.
    # ``storage_size`` is the original top-level key; ``[[workloads.volumes]]``
    # is the declaration operators actually write, and it carries the size the
    # same way it carries mount_path. Gating on storage_size alone meant a
    # workload that declared a pvc volume and nothing else rendered no claim
    # and no mount at all, while still being scheduled as a StatefulSet with
    # the app's data path pointing at an unmounted directory -- a silent
    # data-loss shape, and the app either crashes on the missing mount or
    # writes to the pod's ephemeral FS.
    pvc_volume = (
        next(
            (v for v in w.volumes if v.get("kind", "pvc") == "pvc"),
            None,
        )
        or {}
    )
    storage_size = w.storage_size or pvc_volume.get("size") or ""
    if storage_size:
        claim_name = f"{w.name}-data"
        # Claim name stays derived from the workload name rather than the
        # declaration's ``name``: a StatefulSet's volumeClaimTemplates are
        # immutable and its PVCs are named <claim>-<pod>, so renaming here
        # would orphan the volumes of every already-deployed workload.
        access_mode = pvc_volume.get("access_mode") or "ReadWriteOnce"
        storage_class = w.storage_class or pvc_volume.get("storage_class") or ""
        volume_claim_templates.append(
            {
                "metadata": {"name": claim_name},
                "spec": {
                    "accessModes": [access_mode],
                    "resources": {"requests": {"storage": storage_size}},
                    **({"storageClassName": storage_class} if storage_class else {}),
                },
            }
        )
        # Mount the per-pod claim into the primary container. Without this the
        # volumeClaimTemplate is provisioned but never mounted, so the app's
        # writes land on the pod's ephemeral FS and don't survive a restart
        # (#989). Mount path comes from the workload's pvc volume declaration
        # ([[workloads.volumes]] mount_path), defaulting to /data.
        mount_path = pvc_volume.get("mount_path") or "/data"
        primary = _primary_container(w)
        for container in pod_spec.get("containers", []):
            if primary is not None and container["name"] == primary.name:
                container.setdefault("volumeMounts", []).append({"name": claim_name, "mountPath": mount_path})
        # Make the claim writable by the container regardless of its uid.
        fs_group = w.fs_group if w.fs_group is not None else DEFAULT_PVC_FS_GROUP
        pod_spec.setdefault("securityContext", {})["fsGroup"] = fs_group

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
        headless["spec"]["ports"] = [{"name": "app", "port": primary.port, "targetPort": primary.port}]

    return sts, headless
