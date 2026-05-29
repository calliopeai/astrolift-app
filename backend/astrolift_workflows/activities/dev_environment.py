"""Activities for Calliope App Builder dev environment provisioning (#767, #768).

Three durable units:

* :func:`provision_dev_environment` — render + apply the K8s objects
  that make a dev env serve traffic (Namespace + ConfigMap of uploaded
  files + Deployment + Service + Ingress).
* :func:`sync_dev_environment_files` — update the ConfigMap and bump a
  pod-template annotation so the existing Deployment rolls a fresh pod
  that reads the new file payload.
* :func:`mark_dev_environment_failed` — terminal-state writer used by
  both workflows when the provisioning / sync chain raises.

The implementations mirror the ``provision_namespace`` + ``apply_manifests``
shape used by the app-deploy pipeline (see ``app_lifecycle.py``) so the
provider-driver call path is identical — App Builder dev envs are
"normal" workloads on the tenant cluster, not a parallel runtime.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.dev_environment")

# Resource profile → (cpu_request, memory_request, cpu_limit, memory_limit).
# Three pre-built tiers cover the Builder UX (no per-pod custom sizing).
_RESOURCE_PROFILES: dict[str, tuple[str, str, str, str]] = {
    "small": ("100m", "128Mi", "500m", "256Mi"),
    "medium": ("250m", "256Mi", "1000m", "512Mi"),
    "large": ("500m", "512Mi", "2000m", "1Gi"),
}

# Runtime → (base_image, dep_install_command).
# Each base image is a slim official build to keep cold-start latency
# bounded; static is nginx since the Builder renders client-only HTML
# bundles for "static" projects.
_RUNTIME_IMAGES: dict[str, tuple[str, str]] = {
    "python": ("python:3.11-slim", "pip install -r requirements.txt --quiet || true"),
    "node": ("node:20-slim", "npm install --quiet || true"),
    "ruby": ("ruby:3.3-slim", "bundle install --quiet || true"),
    "go": ("golang:1.22-alpine", "go mod download || true"),
    "static": ("nginx:alpine", ""),
}


def _base_domain_for_org(org_slug: str) -> str:
    """Return the apex DNS suffix to use for builder preview URLs.

    Reads ``settings.BUILDER_BASE_DOMAIN`` when present (operator can
    pin it via env on the control plane) and falls back to
    ``<org-slug>.dev.astrolift.io`` so a vanilla deploy still produces
    a reachable hostname. The actual delegation of the zone to the
    cluster's ingress controller is an out-of-band DNS concern — the
    activity only constructs the URL.
    """
    from django.conf import settings

    return getattr(settings, "BUILDER_BASE_DOMAIN", "") or f"{org_slug}.dev.astrolift.io"


def _resolve_image(runtime: str, runtime_version: str) -> tuple[str, str]:
    """Pick a base image + dep-install command for a runtime row.

    Splits the default tag (e.g. ``3.11-slim``) on ``-`` so an operator-
    specified ``runtime_version="3.12"`` becomes ``python:3.12-slim``
    while keeping the slim suffix. ``static`` ignores the version
    knob — nginx tag is platform-controlled.
    """
    base_image, dep_cmd = _RUNTIME_IMAGES.get(runtime, _RUNTIME_IMAGES["python"])
    if runtime_version and runtime != "static":
        repo, _, tag = base_image.partition(":")
        suffix_parts = tag.split("-", 1)
        new_tag = runtime_version + ("-" + suffix_parts[1] if len(suffix_parts) > 1 else "")
        base_image = f"{repo}:{new_tag}"
    return base_image, dep_cmd


def _build_manifests(dev: Any) -> tuple[str, str, list[dict[str, Any]]]:
    """Build the namespace, preview URL, and rendered manifest list for ``dev``.

    Split out from the activity body so the same render runs for both
    the initial provision and the file-sync rolling restart (which
    re-applies the ConfigMap + Deployment patch).
    """
    namespace = f"builder-dev-{dev.guid[:8]}"
    org_slug = dev.organization.slug
    runtime = dev.runtime or "python"
    base_image, dep_cmd = _resolve_image(runtime, dev.runtime_version or "")

    cpu_req, mem_req, cpu_lim, mem_lim = _RESOURCE_PROFILES.get(
        dev.resource_profile, _RESOURCE_PROFILES["small"]
    )
    port = dev.port or 8080
    start_cmd = dev.start_command or "echo 'no start_command set; sleeping' && sleep infinity"
    base_domain = _base_domain_for_org(org_slug)
    hostname = f"dev-{dev.guid[:8]}.{base_domain}"
    preview_url = f"https://{hostname}"

    # ConfigMap can't be empty in some k8s versions and an empty data
    # block makes the deployment fail mount; carry a single placeholder
    # key when the file tree is empty so subsequent syncs flip in
    # actual content without re-creating the resource.
    cm_data = dict(dev.files or {})
    cm_manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "builder-files", "namespace": namespace},
        "data": cm_data if cm_data else {"__placeholder": ""},
    }

    env_vars_list = [{"name": str(k), "value": str(v)} for k, v in (dev.env_vars or {}).items()]
    env_vars_list.append({"name": "PORT", "value": str(port)})

    init_containers: list[dict[str, Any]] = []
    if dep_cmd:
        init_containers = [
            {
                "name": "install-deps",
                "image": base_image,
                "command": ["sh", "-c", dep_cmd],
                "volumeMounts": [
                    {"name": "app-files", "mountPath": "/app"},
                    {"name": "deps-cache", "mountPath": "/tmp/deps"},
                ],
                "workingDir": "/app",
            }
        ]

    deployment_manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "builder-dev",
            "namespace": namespace,
            "annotations": {"builder.astrolift.io/dev-env-guid": dev.guid},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "builder-dev"}},
            "template": {
                "metadata": {"labels": {"app": "builder-dev"}},
                "spec": {
                    "initContainers": init_containers,
                    "containers": [
                        {
                            "name": "app",
                            "image": base_image,
                            "command": ["sh", "-c", start_cmd],
                            "ports": [{"containerPort": port}],
                            "env": env_vars_list,
                            "resources": {
                                "requests": {"cpu": cpu_req, "memory": mem_req},
                                "limits": {"cpu": cpu_lim, "memory": mem_lim},
                            },
                            "volumeMounts": [{"name": "app-files", "mountPath": "/app"}],
                            "workingDir": "/app",
                        }
                    ],
                    "volumes": [
                        {"name": "app-files", "configMap": {"name": "builder-files"}},
                        {"name": "deps-cache", "emptyDir": {}},
                    ],
                },
            },
        },
    }

    service_manifest = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "builder-dev", "namespace": namespace},
        "spec": {
            "selector": {"app": "builder-dev"},
            "ports": [{"port": 80, "targetPort": port}],
        },
    }

    return (
        namespace,
        preview_url,
        [
            cm_manifest,
            deployment_manifest,
            service_manifest,
            _ingress_manifest(namespace, hostname, dev),
        ],
    )


def _ingress_manifest(namespace: str, hostname: str, dev: Any) -> dict[str, Any]:
    cluster = dev.tenant_cluster
    ingress_class = (cluster.ingress_class if cluster is not None else "") or "nginx"
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": "builder-dev",
            "namespace": namespace,
            "annotations": {
                "kubernetes.io/ingress.class": ingress_class,
                # WebSocket / long-poll friendliness — the builder
                # often serves dev servers with hot-reload streams.
                "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
            },
        },
        "spec": {
            "rules": [
                {
                    "host": hostname,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": "builder-dev",
                                        "port": {"number": 80},
                                    },
                                },
                            }
                        ],
                    },
                }
            ],
        },
    }


def _provision_dev_environment_sync(dev_environment_id: int) -> dict[str, Any]:
    """Create the K8s resources backing a dev environment and persist its preview URL."""
    from astrolift_lifecycle.models import DevEnvironment
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    dev = DevEnvironment.all_objects.select_related("organization", "tenant_cluster").get(
        pk=dev_environment_id
    )

    cluster = dev.tenant_cluster
    if cluster is None:
        raise RuntimeError(f"dev environment {dev.guid} has no tenant_cluster assigned")

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)

    namespace, preview_url, resources = _build_manifests(dev)

    labels = {
        "astrolift.io/managed-by": "astrolift",
        "astrolift.io/organization": dev.organization.slug,
        "astrolift.io/dev-env": dev.guid,
    }
    annotations = {"astrolift.io/dev-env-id": str(dev.pk)}
    driver.ensure_namespace(ctx.slug, namespace, labels, annotations)

    result = driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
        raise RuntimeError(
            f"apply_manifests failed for dev environment {dev.guid}: " + "; ".join(result.summary())
        )

    DevEnvironment.objects.filter(pk=dev.pk).update(
        namespace=namespace,
        preview_url=preview_url,
        status=DevEnvironment.Status.RUNNING,
        error_message="",
    )
    return {"namespace": namespace, "preview_url": preview_url}


@activity.defn(name="astrolift.builder.provision_dev_environment")
async def provision_dev_environment(dev_environment_id: int) -> dict:
    """Provision a dev environment's K8s namespace + workload.

    Idempotent: ``ensure_namespace`` + ``apply_manifests`` are
    server-side-apply equivalents on every cluster driver, so re-runs
    converge rather than fail.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_provision_dev_environment_sync)(dev_environment_id)


def _mark_dev_environment_failed_sync(dev_environment_id: int, error: str) -> None:
    from astrolift_lifecycle.models import DevEnvironment

    DevEnvironment.objects.filter(pk=dev_environment_id).update(
        status=DevEnvironment.Status.FAILED,
        error_message=error[:2000],
    )


@activity.defn(name="astrolift.builder.mark_dev_environment_failed")
async def mark_dev_environment_failed(dev_environment_id: int, error: str) -> None:
    """Flip a dev env to FAILED and persist the error message.

    Called from the workflow catch-all so the operator-facing status
    surfaces the cause without scraping Temporal history.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_dev_environment_failed_sync)(dev_environment_id, error)


def _sync_dev_environment_files_sync(dev_environment_id: int) -> None:
    """Update the ConfigMap with the new file tree and force a rolling restart.

    Re-apply of the ConfigMap propagates the new file payload, but k8s
    does NOT roll the pod automatically when a mounted ConfigMap
    changes — pods see the new file contents only on next start, with
    some delay even then. Patching ``spec.template.metadata.annotations``
    forces a rolling restart so the new content is live immediately.
    """
    from astrolift_lifecycle.models import DevEnvironment
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    dev = DevEnvironment.all_objects.select_related("organization", "tenant_cluster").get(
        pk=dev_environment_id
    )
    cluster = dev.tenant_cluster
    if cluster is None:
        raise RuntimeError(f"dev environment {dev.guid} has no tenant_cluster")

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    namespace = dev.namespace
    if not namespace:
        msg = f"dev environment {dev.guid} has no namespace — " "provision step never landed; sync cannot run"
        raise RuntimeError(msg)

    cm_data = dict(dev.files or {})
    cm_manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "builder-files", "namespace": namespace},
        "data": cm_data if cm_data else {"__placeholder": ""},
    }

    restart_ts = str(int(time.time()))
    deploy_patch = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "builder-dev", "namespace": namespace},
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"builder.astrolift.io/last-sync": restart_ts},
                },
            },
        },
    }

    result = driver.apply_manifests(ctx.slug, namespace, [cm_manifest, deploy_patch])
    if not result.ok:
        raise RuntimeError(
            f"sync files failed for dev environment {dev.guid}: " + "; ".join(result.summary())
        )

    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)


@activity.defn(name="astrolift.builder.sync_dev_environment_files")
async def sync_dev_environment_files(dev_environment_id: int) -> None:
    """Push a new file tree to a running dev environment.

    Re-applies the ConfigMap and bumps a pod-template annotation to
    force a rolling restart so the new contents take effect immediately.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_sync_dev_environment_files_sync)(dev_environment_id)
