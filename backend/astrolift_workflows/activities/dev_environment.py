"""Builder provisioning, sync and promotion on the tenant cluster.

The control plane snapshots files and the SQLite seed into a private immutable
artifact. A runtime-neutral init container verifies and installs that artifact;
Kubernetes holds only runtime configuration and a scoped download credential.
Promoted apps keep their separate namespace and optional persistent data volume.
"""

from __future__ import annotations

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

# The data volume remains separate from immutable app files.
_DATA_DIR = "/data"
_DATA_PART_PREFIX = "builder-data-part-"


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


def _data_volume_size_gib(nbytes: int) -> int:
    """PVC size for a data file of ``nbytes``: room for the app's writes
    beyond the shipped file, never smaller than the file itself.

    Shared by the renderer (the claim it emits) and the Workload row
    persisted for a promoted app (#1875), so the two never disagree about
    what was actually requested.
    """
    return max(1, math.ceil(4 * nbytes / 2**30))


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

    import json

    from astrolift_lifecycle.builder_artifacts import init_script, reference

    artifact = reference(dev)
    credential = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": "builder-artifact-" + artifact["sha256"][:20], "namespace": namespace},
        "stringData": {"config": json.dumps(artifact)},
    }
    env_vars_list = [{"name": str(k), "value": str(v)} for k, v in (dev.env_vars or {}).items()]
    env_vars_list.append({"name": "PORT", "value": str(port)})
    app_mounts = [{"name": "app-files", "mountPath": "/app", "readOnly": True}]
    volumes = [
        {"name": "app-files", "emptyDir": {}},
        {"name": "deps-cache", "emptyDir": {}},
        {"name": "artifact", "secret": {"secretName": "builder-artifact-" + artifact["sha256"][:20]}},
    ]
    seed_mounts = [
        {"name": "app-files", "mountPath": "/app"},
        {"name": "artifact", "mountPath": "/artifact", "readOnly": True},
    ]
    data_resources = []
    if dev.data_file_path:
        data_file = posixpath.join(_DATA_DIR, dev.data_file_path)
        if data_storage_class:
            claim = f"{name}-data"
            data_resources.append(
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": claim, "namespace": namespace},
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "storageClassName": data_storage_class,
                        "resources": {
                            "requests": {
                                "storage": f"{_data_volume_size_gib(len(bytes(dev.data_file or b'')))}Gi"
                            }
                        },
                    },
                }
            )
            volumes.append({"name": "data", "persistentVolumeClaim": {"claimName": claim}})
        else:
            volumes.append({"name": "data", "emptyDir": {}})
        env_vars_list.extend(
            [
                {"name": "ASTROLIFT_DATA_DIR", "value": _DATA_DIR},
                {"name": "ASTROLIFT_DATA_FILE", "value": data_file},
            ]
        )
        app_mounts.append({"name": "data", "mountPath": _DATA_DIR})
        seed_mounts.append({"name": "data", "mountPath": _DATA_DIR})
    init_containers = [
        {
            "name": "fetch-artifact",
            "image": "python:3.12-slim",
            "command": ["python", "-c", init_script()],
            "volumeMounts": seed_mounts,
        }
    ]

    if dep_cmd:
        init_containers.append(
            {
                "name": "install-deps",
                "image": base_image,
                "command": ["sh", "-c", dep_cmd],
                "volumeMounts": [
                    {"name": "app-files", "mountPath": "/app", "readOnly": True},
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
        credential,
        *data_resources,
        deployment_manifest,
        service_manifest,
        _ingress_manifest(namespace, hostname, dev, name),
    ]


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
        msg = f"dev environment {dev.guid} has no namespace — provision step never landed; sync cannot run"
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

    # The dev env's own preview is untouched by also serving its files into
    # the app namespace, so its status should say so: RUNNING, same as
    # before promote (#1875). Left at PROMOTING forever, neither a files
    # sync (which requires RUNNING) nor a second promote (its own
    # precondition) could ever run again for this dev env.
    DevEnvironment.objects.filter(pk=dev.pk).update(
        status=DevEnvironment.Status.RUNNING,
        error_message="",
    )
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


def _persist_promoted_workload(app: Any, dev: Any, storage_class: str) -> Any:
    """Create or update the one Workload row a promoted app's runtime
    renders to (#1875), so the app pages, rollback and observability see
    it the same way they see a manifest-driven deploy's workloads.

    One workload per promoted app, slug ``"app"``: ``_render_runtime``
    always renders exactly one Deployment/Service/Ingress set for it
    (``name="builder-app"``), so there is exactly one row to describe.
    """
    from astrolift_registry.models import Workload

    cpu_req, mem_req, cpu_lim, mem_lim = _RESOURCE_PROFILES.get(
        dev.resource_profile, _RESOURCE_PROFILES["small"]
    )
    storage_size = ""
    if storage_class and dev.data_file_path:
        storage_size = f"{_data_volume_size_gib(len(bytes(dev.data_file or b'')))}Gi"

    fields = {
        "kind": Workload.Kind.DEPLOYMENT,
        "is_public": True,
        "replicas": 1,
        "cpu_request": cpu_req,
        "cpu_limit": cpu_lim,
        "memory_request": mem_req,
        "memory_limit": mem_lim,
        "storage_class": storage_class,
        "storage_size": storage_size,
    }
    workload = Workload.objects.filter(registered_app=app, slug="app", deleted_at__isnull=True).first()
    if workload is None:
        return Workload.objects.create(registered_app=app, name="app", slug="app", **fields)
    if any(getattr(workload, key) != value for key, value in fields.items()):
        for key, value in fields.items():
            setattr(workload, key, value)
        workload.save()
    return workload


def _record_promoted_app_deployment_sync(dev_environment_id: int, storage_class: str) -> dict[str, Any]:
    """Record the runtime ``deploy_promoted_app`` just applied as a Workload
    + Deployment (#1875), so the app's own pages, rollback and
    observability pick it up the same way they do a manifest-driven
    deploy's.

    A separate activity rather than folded into ``deploy_promoted_app``'s
    body: the workflow gates *scheduling* it behind ``workflow.patched`` (a
    determinism concern for a deploy already in flight when this shipped),
    which only applies to a workflow's own sequence of activity calls, not
    to what one activity does internally -- so the status flip-back above
    needed no such gate, but this new call does.
    """
    from astrolift_lifecycle.models import AppEnvironment, Deployment, DevEnvironment
    from astrolift_workflows.activities.app_lifecycle import _mark_running_sync

    dev = DevEnvironment.all_objects.select_related("promoted_app", "tenant_cluster").get(
        pk=dev_environment_id
    )
    app = dev.promoted_app
    if app is None:
        raise RuntimeError(f"dev environment {dev.guid} has no promoted app")

    workload = _persist_promoted_workload(app, dev, storage_class)

    # deploy_promoted_app always renders into namespace_for_app(app) (#1858),
    # which is where the first environment created on the dev's cluster
    # lives (blank k8s_namespace, #1922) -- the same one every promote of
    # this dev env has ever targeted.
    env = (
        AppEnvironment.objects.filter(
            registered_app=app, tenant_cluster=dev.tenant_cluster, deleted_at__isnull=True
        )
        .order_by("created_at")
        .first()
    )
    if env is None:
        raise RuntimeError(f"promoted app {app.slug} has no environment to record a deployment against")

    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        workload=workload,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        ci_actor_kind="builder_promote",
        status=Deployment.Status.PENDING.value,
    )
    deployment.transition_to(Deployment.Status.DEPLOYING)
    # Reused, not reimplemented (rollback_deployment.py's rationale applies
    # here too): supersedes the app's prior RUNNING deploy in this env under
    # the same lock a concurrent real deploy would use, so rollback's "most
    # recent prior running/superseded" lookup keeps working across repeated
    # promotes exactly as it does for a manifest-driven app.
    _mark_running_sync(deployment.pk)
    return {"workload_id": workload.pk, "deployment_id": deployment.pk}


@activity.defn(name="astrolift.builder.record_promoted_app_deployment")
async def record_promoted_app_deployment(dev_environment_id: int, storage_class: str) -> dict:
    """Persist the Workload + Deployment rows for a promoted app's runtime (#1875).

    Idempotent enough for Temporal's at-least-once activities the same way
    ``mark_running`` already is for a normal deploy: the Workload is
    upserted by slug, and a retried Deployment create producing an extra row
    is the same accepted risk ``create_rollback_deployment`` /
    ``create_promotion_deployment`` already carry.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_record_promoted_app_deployment_sync)(dev_environment_id, storage_class)
