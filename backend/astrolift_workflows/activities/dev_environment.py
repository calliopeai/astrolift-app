"""Activities for Calliope App Builder dev environment provisioning (#767, #768).

Four durable units:

* :func:`provision_dev_environment`: render + apply the K8s objects
  that make a dev env serve traffic (Namespace + ConfigMap of uploaded
  files + Deployment + Service + Ingress).
* :func:`sync_dev_environment_files`: re-apply the rendered objects with
  a bumped pod-template annotation so the existing Deployment rolls a
  fresh pod that reads the new file payload.
* :func:`deploy_promoted_app`: apply the same runtime into the promoted
  app's own namespace (#1858), with its data volume on a PVC when the
  cluster can provision one.
* :func:`mark_dev_environment_failed`: terminal-state writer used by
  the workflows when the provisioning / sync chain raises.

The implementations mirror the ``provision_namespace`` + ``apply_manifests``
shape used by the app-deploy pipeline (see ``app_lifecycle.py``) so the
provider-driver call path is identical — App Builder dev envs are
"normal" workloads on the tenant cluster, not a parallel runtime.

The declared data file (#1858) is too large for the files ConfigMap, so it
ships as numbered Secrets that a ``seed-data`` init container concatenates
into the data volume, and only when the file is not already there: an
emptyDir starts from the shipped copy on every new pod, a PVC keeps what
the app wrote.
"""

from __future__ import annotations

import base64
import logging
import math
import posixpath
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

# Where the data volume mounts, and where the seed Secrets project into the
# init container that copies the data file onto it.
_DATA_DIR = "/data"
_SEED_DIR = "/seed"
_DATA_PART_PREFIX = "builder-data-part-"
# A Secret holds at most 1 MiB of data. Parts stay well under that so the
# server-side apply request, which carries them base64-encoded, fits too.
_DATA_PART_BYTES = 900 * 1024


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


def promoted_app_hostname(app: Any) -> str:
    """Hostname a promoted builder app serves on (#1858).

    The app namespace is ``<org-slug>-<app-slug>`` folded to one DNS label,
    so it is unique across the install even when ``BUILDER_BASE_DOMAIN``
    puts every org under one zone; an app slug alone is unique only per org.
    """
    from core.app_deploy import namespace_for_app

    return f"{namespace_for_app(app)}.{_base_domain_for_org(app.organization.slug)}"


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


def _data_parts(data: bytes) -> list[bytes]:
    """Split a data file into Secret-sized parts; an empty file is one empty part."""
    return [data[i : i + _DATA_PART_BYTES] for i in range(0, len(data), _DATA_PART_BYTES)] or [b""]


def _dev_env_names(dev: Any) -> tuple[str, str]:
    """``(namespace, hostname)`` for a dev environment.

    Built from the whole guid: a UUIDv7's leading hex is its timestamp, so
    a prefix is shared by every dev env created in the same ~65 seconds,
    in any org, and two of them would land in one namespace.
    """
    guid_hex = str(dev.guid).replace("-", "")
    return f"builder-dev-{guid_hex}", f"dev-{guid_hex}.{_base_domain_for_org(dev.organization.slug)}"


def _render_runtime(
    dev: Any,
    *,
    namespace: str,
    name: str,
    hostname: str,
    data_storage_class: str = "",
    restart_stamp: str = "",
) -> list[dict[str, Any]]:
    """Render the objects that serve ``dev``'s file tree.

    Shared by the dev environment and the app it is promoted to (#1858).
    Those differ in namespace, object names, host, and whether the data
    volume is a PVC (``data_storage_class`` set) or an emptyDir.
    ``restart_stamp`` goes on the pod template so a re-apply rolls the pod.
    """
    runtime = dev.runtime or "python"
    base_image, dep_cmd = _resolve_image(runtime, dev.runtime_version or "")

    cpu_req, mem_req, cpu_lim, mem_lim = _RESOURCE_PROFILES.get(
        dev.resource_profile, _RESOURCE_PROFILES["small"]
    )
    port = dev.port or 8080
    start_cmd = dev.start_command or "echo 'no start_command set; sleeping' && sleep infinity"

    # ConfigMap can't be empty in some k8s versions and an empty data
    # block makes the deployment fail mount; carry a single placeholder
    # key when the file tree is empty so subsequent syncs flip in
    # actual content without re-creating the resource. Binary files
    # (#1858) go under ``binaryData``, which takes base64 as uploaded.
    # ConfigMap keys are flat (``[-._a-zA-Z0-9]+``), so a path with a
    # directory is stored under a generated key and the volume's ``items``
    # put it back at its path; the kubelet creates the directories (#1873).
    text_files: dict[str, Any] = {}
    binary_files: dict[str, str] = {}
    items: list[dict[str, str]] = []
    for index, (path, value) in enumerate(sorted((dev.files or {}).items())):
        key = f"f{index:04d}"
        items.append({"key": key, "path": path})
        if isinstance(value, dict):
            binary_files[key] = value["content"]
        else:
            text_files[key] = value
    cm_manifest: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "builder-files", "namespace": namespace},
        "data": text_files if (text_files or binary_files) else {"__placeholder": ""},
    }
    if binary_files:
        cm_manifest["binaryData"] = binary_files

    env_vars_list = [{"name": str(k), "value": str(v)} for k, v in (dev.env_vars or {}).items()]
    env_vars_list.append({"name": "PORT", "value": str(port)})
    app_mounts: list[dict[str, Any]] = [{"name": "app-files", "mountPath": "/app"}]
    volumes: list[dict[str, Any]] = [
        {"name": "app-files", "configMap": {"name": "builder-files", **({"items": items} if items else {})}},
        {"name": "deps-cache", "emptyDir": {}},
    ]

    init_containers: list[dict[str, Any]] = []
    data_resources: list[dict[str, Any]] = []
    if dev.data_file_path:
        data_file = posixpath.join(_DATA_DIR, dev.data_file_path)
        data = bytes(dev.data_file or b"")
        parts = _data_parts(data)
        part_names = [f"{_DATA_PART_PREFIX}{i:04d}" for i in range(len(parts))]
        data_resources = [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "Opaque",
                "metadata": {"name": part_name, "namespace": namespace},
                "data": {"part": base64.b64encode(part).decode("ascii")},
            }
            for part_name, part in zip(part_names, parts, strict=True)
        ]
        if data_storage_class:
            claim = f"{name}-data"
            # Room for the app's writes beyond the shipped file; never
            # smaller than the file itself.
            size_gib = max(1, math.ceil(4 * len(data) / 2**30))
            data_resources.append(
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": claim, "namespace": namespace},
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "storageClassName": data_storage_class,
                        "resources": {"requests": {"storage": f"{size_gib}Gi"}},
                    },
                }
            )
            volumes.append({"name": "data", "persistentVolumeClaim": {"claimName": claim}})
        else:
            volumes.append({"name": "data", "emptyDir": {}})
        volumes.append(
            {
                "name": "data-seed",
                "projected": {
                    "sources": [
                        {"secret": {"name": part_name, "items": [{"key": "part", "path": part_name}]}}
                        for part_name in part_names
                    ]
                },
            }
        )
        data_env = [
            {"name": "ASTROLIFT_DATA_DIR", "value": _DATA_DIR},
            {"name": "ASTROLIFT_DATA_FILE", "value": data_file},
        ]
        env_vars_list.extend(data_env)
        app_mounts.append({"name": "data", "mountPath": _DATA_DIR})
        init_containers.append(
            {
                "name": "seed-data",
                "image": base_image,
                "command": ["sh", "-c", _seed_command(part_names)],
                "env": data_env,
                "volumeMounts": [
                    {"name": "data", "mountPath": _DATA_DIR},
                    {"name": "data-seed", "mountPath": _SEED_DIR, "readOnly": True},
                ],
            }
        )

    if dep_cmd:
        init_containers.append(
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
        )

    template_metadata: dict[str, Any] = {"labels": {"app": name}}
    if restart_stamp:
        template_metadata["annotations"] = {"builder.astrolift.io/last-sync": restart_stamp}
    deployment_spec: dict[str, Any] = {
        "replicas": 1,
        "selector": {"matchLabels": {"app": name}},
        "template": {
            "metadata": template_metadata,
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
                        "volumeMounts": app_mounts,
                        "workingDir": "/app",
                    }
                ],
                "volumes": volumes,
            },
        },
    }
    if data_storage_class:
        # A ReadWriteOnce claim attaches to one node; a rolling update
        # that schedules the new pod elsewhere would wait on it forever.
        deployment_spec["strategy"] = {"type": "Recreate"}
    deployment_manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "annotations": {"builder.astrolift.io/dev-env-guid": str(dev.guid)},
        },
        "spec": deployment_spec,
    }

    service_manifest = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "selector": {"app": name},
            "ports": [{"port": 80, "targetPort": port}],
        },
    }

    return [
        cm_manifest,
        *data_resources,
        deployment_manifest,
        service_manifest,
        _ingress_manifest(namespace, hostname, dev, name),
    ]


def _seed_command(part_names: list[str]) -> str:
    """Shell for the ``seed-data`` init container.

    Writes ``$ASTROLIFT_DATA_FILE`` from the projected parts only when it
    is absent, through a ``.partial`` rename so a pod killed mid-copy never
    leaves a truncated file that the next start would take as seeded.
    """
    parts = " ".join(f"{_SEED_DIR}/{part_name}" for part_name in part_names)
    return (
        'set -e; if [ ! -e "$ASTROLIFT_DATA_FILE" ]; then '
        'mkdir -p "$(dirname "$ASTROLIFT_DATA_FILE")"; '
        f'cat {parts} > "$ASTROLIFT_DATA_FILE.partial"; '
        'mv "$ASTROLIFT_DATA_FILE.partial" "$ASTROLIFT_DATA_FILE"; fi'
    )


def _build_manifests(dev: Any, *, restart_stamp: str = "") -> tuple[str, str, list[dict[str, Any]]]:
    """Build the namespace, preview URL, and rendered manifest list for ``dev``.

    Split out from the activity body so the same render runs for both
    the initial provision and the file-sync rolling restart.
    """
    namespace, hostname = _dev_env_names(dev)
    resources = _render_runtime(
        dev,
        namespace=namespace,
        name="builder-dev",
        hostname=hostname,
        restart_stamp=restart_stamp,
    )
    return namespace, f"https://{hostname}", resources


def _ingress_manifest(namespace: str, hostname: str, dev: Any, name: str) -> dict[str, Any]:
    cluster = dev.tenant_cluster
    ingress_class = (cluster.ingress_class if cluster is not None else "") or "nginx"
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": name,
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
                                        "name": name,
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
        "astrolift.io/dev-env": str(dev.guid),
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
    """Re-apply the dev environment's objects and force a rolling restart.

    k8s does NOT roll the pod when a mounted ConfigMap or Secret
    changes; pods see new contents only on next start, with some delay
    even then. So the pod-template annotation carries a fresh stamp.
    The full render goes out rather than a patch: server-side apply
    treats the manifest as everything the field manager owns, so a
    Deployment carrying only the annotation would drop the rest of the
    spec. Data parts a smaller or removed data file no longer uses are
    deleted afterwards.
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

    _namespace, hostname = _dev_env_names(dev)
    resources = _render_runtime(
        dev,
        namespace=namespace,
        name="builder-dev",
        hostname=hostname,
        restart_stamp=str(int(time.time())),
    )
    result = driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
        raise RuntimeError(
            f"sync files failed for dev environment {dev.guid}: " + "; ".join(result.summary())
        )

    kept = {r["metadata"]["name"] for r in resources if r["kind"] == "Secret"}
    stale = [
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": namespace}}
        for name in (
            (secret.get("metadata") or {}).get("name", "")
            for secret in driver.list_manifests(ctx.slug, namespace, "Secret")
        )
        if name.startswith(_DATA_PART_PREFIX) and name not in kept
    ]
    if stale:
        driver.delete_manifests(ctx.slug, namespace, stale)

    DevEnvironment.objects.filter(pk=dev.pk).update(status=DevEnvironment.Status.RUNNING)


@activity.defn(name="astrolift.builder.sync_dev_environment_files")
async def sync_dev_environment_files(dev_environment_id: int) -> None:
    """Push a new file tree to a running dev environment.

    Re-applies the rendered objects with a bumped pod-template
    annotation to force a rolling restart so the new contents take
    effect immediately.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_sync_dev_environment_files_sync)(dev_environment_id)


def _deploy_promoted_app_sync(dev_environment_id: int, storage_class: str) -> dict[str, Any]:
    """Serve the promoted app from its own namespace (#1858).

    Renders the dev environment's runtime (files, data file, start
    command) into the app namespace. ``storage_class`` is what promote
    found the cluster can provision; empty means the data volume is an
    emptyDir, which the promote response already reported as
    ``data_persistent: false``. The namespace labels match
    ``provision_namespace`` so the onboarding that runs alongside
    converges on the same object.
    """
    from astrolift_lifecycle.models import DevEnvironment
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    dev = DevEnvironment.all_objects.select_related(
        "organization", "tenant_cluster", "promoted_app__organization"
    ).get(pk=dev_environment_id)
    app = dev.promoted_app
    if app is None:
        raise RuntimeError(f"dev environment {dev.guid} has no promoted app")
    cluster = dev.tenant_cluster
    if cluster is None:
        raise RuntimeError(f"dev environment {dev.guid} has no tenant_cluster")

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    namespace = namespace_for_app(app)
    hostname = promoted_app_hostname(app)

    labels = {
        "astrolift.io/managed-by": "astrolift",
        "astrolift.io/organization": app.organization.slug,
        "astrolift.io/app": app.slug,
    }
    annotations = {"astrolift.io/registered-app-id": str(app.pk)}
    driver.ensure_namespace(ctx.slug, namespace, labels, annotations)

    resources = _render_runtime(
        dev,
        namespace=namespace,
        name="builder-app",
        hostname=hostname,
        data_storage_class=storage_class,
    )
    result = driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
        raise RuntimeError(f"deploy failed for promoted app {app.slug}: " + "; ".join(result.summary()))
    return {"namespace": namespace, "app_url": f"https://{hostname}"}


@activity.defn(name="astrolift.builder.deploy_promoted_app")
async def deploy_promoted_app(dev_environment_id: int, storage_class: str) -> dict:
    """Apply a promoted builder app's runtime into its namespace (#1858).

    Idempotent for the same reason as the provision activity: every
    object goes through ``ensure_namespace`` / ``apply_manifests``.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_deploy_promoted_app_sync)(dev_environment_id, storage_class)
