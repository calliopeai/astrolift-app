"""Portable managed-filesystem rendering for app and agent pods."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from _sdk.secrets import SecretReferenceError, resolve_secret_reference


class FilesystemBindingError(ValueError):
    """A persisted volume binding cannot be attached safely."""


def preflight_bindings(
    bindings: Iterable[Any],
    *,
    cluster_driver: Any,
    cluster_slug: str,
    namespace: str,
) -> None:
    """Prove every referenced CSI driver or existing claim before apply."""

    rows = list(bindings)
    csi_drivers: set[str] | None = None
    storage_classes: dict[str, Any] | None = None
    for binding in rows:
        _validate_persisted_binding(binding)
        service = binding.managed_service
        if hasattr(service, "effective_cluster"):
            service_cluster = service.effective_cluster
            if service_cluster is None:
                raise FilesystemBindingError(
                    f"filesystem {service.name!r} has no provisioning cluster",
                )
            if str(service_cluster.slug) != cluster_slug:
                raise FilesystemBindingError(
                    f"filesystem {service.name!r} is provisioned on cluster "
                    f"{service_cluster.slug!r}, not runtime cluster {cluster_slug!r}",
                )
        if str(binding.source_kind) == "csi":
            if csi_drivers is None:
                list_csi = getattr(cluster_driver, "list_csi_drivers", None)
                if not callable(list_csi):
                    raise FilesystemBindingError(
                        f"cluster {cluster_slug!r} cannot inventory CSI drivers; refusing filesystem mounts",
                    )
                try:
                    csi_drivers = set(list_csi(cluster_slug))
                except Exception as exc:
                    raise FilesystemBindingError(
                        f"cluster {cluster_slug!r} CSI-driver preflight failed: {exc}",
                    ) from exc
            if str(binding.csi_driver) not in csi_drivers:
                raise FilesystemBindingError(
                    f"filesystem {binding.managed_service.name!r} requires CSI driver "
                    f"{binding.csi_driver!r}, but cluster {cluster_slug!r} reports {sorted(csi_drivers)!r}",
                )
        elif str(binding.source_kind) == "existing_pvc":
            if str(binding.claim_namespace) != namespace:
                raise FilesystemBindingError(
                    f"filesystem {binding.managed_service.name!r} claim {binding.claim_name!r} is in namespace "
                    f"{binding.claim_namespace!r}, not {namespace!r}",
                )
            claim_exists = getattr(cluster_driver, "persistent_volume_claim_exists", None)
            if not callable(claim_exists):
                raise FilesystemBindingError(
                    f"cluster {cluster_slug!r} cannot preflight PVCs; refusing filesystem mounts",
                )
            try:
                exists = bool(claim_exists(cluster_slug, namespace, str(binding.claim_name)))
            except Exception as exc:
                raise FilesystemBindingError(
                    f"filesystem claim preflight failed for {binding.claim_name!r}: {exc}",
                ) from exc
            if not exists:
                raise FilesystemBindingError(
                    f"filesystem {binding.managed_service.name!r} claim {binding.claim_name!r} does not exist "
                    f"in namespace {namespace!r}",
                )
        elif str(binding.source_kind) == "dynamic_pvc":
            if storage_classes is None:
                list_storage_classes = getattr(cluster_driver, "list_storage_classes", None)
                if not callable(list_storage_classes):
                    raise FilesystemBindingError(
                        f"cluster {cluster_slug!r} cannot inventory StorageClasses; refusing dynamic claims",
                    )
                try:
                    storage_classes = {
                        str(getattr(row, "name", "") or ""): row
                        for row in list_storage_classes(cluster_slug)
                        if getattr(row, "name", "")
                    }
                except Exception as exc:
                    raise FilesystemBindingError(
                        f"cluster {cluster_slug!r} StorageClass preflight failed: {exc}",
                    ) from exc
            storage_class = storage_classes.get(str(binding.storage_class_name))
            if storage_class is None:
                raise FilesystemBindingError(
                    f"filesystem {binding.managed_service.name!r} requires StorageClass "
                    f"{binding.storage_class_name!r}, but cluster {cluster_slug!r} reports "
                    f"{sorted(storage_classes)!r}",
                )
            if str(binding.csi_driver):
                provisioner = str(getattr(storage_class, "provisioner", "") or "")
                if not provisioner:
                    raise FilesystemBindingError(
                        f"filesystem {binding.managed_service.name!r} cannot verify the provisioner "
                        f"for StorageClass {binding.storage_class_name!r}",
                    )
                if provisioner != str(binding.csi_driver):
                    raise FilesystemBindingError(
                        f"filesystem {binding.managed_service.name!r} requires StorageClass "
                        f"{binding.storage_class_name!r} to use provisioner {binding.csi_driver!r}, "
                        f"but it uses {provisioner!r}",
                    )
                if csi_drivers is None:
                    list_csi = getattr(cluster_driver, "list_csi_drivers", None)
                    if not callable(list_csi):
                        raise FilesystemBindingError(
                            f"cluster {cluster_slug!r} cannot inventory CSI drivers; refusing dynamic claims",
                        )
                    try:
                        csi_drivers = set(list_csi(cluster_slug))
                    except Exception as exc:
                        raise FilesystemBindingError(
                            f"cluster {cluster_slug!r} CSI-driver preflight failed: {exc}",
                        ) from exc
                if str(binding.csi_driver) not in csi_drivers:
                    raise FilesystemBindingError(
                        f"filesystem {binding.managed_service.name!r} requires CSI driver "
                        f"{binding.csi_driver!r}, but cluster {cluster_slug!r} reports {sorted(csi_drivers)!r}",
                    )
        else:
            raise FilesystemBindingError(
                f"filesystem {binding.managed_service.name!r} has unsupported source {binding.source_kind!r}",
            )


def resolve_binding_secret_manifests(
    bindings: Iterable[Any],
    *,
    secrets_backend: Any,
    namespace: str,
    consumer_key: str,
) -> list[dict[str, Any]]:
    """Resolve CSI credential references into consumer-scoped Secrets."""

    resources: list[dict[str, Any]] = []
    for binding in bindings:
        secret_refs = dict(binding.secret_refs or {})
        secret_literals = dict(getattr(binding, "secret_literals", None) or {})
        if not secret_refs and not secret_literals:
            continue
        volume_data: dict[str, str] = {str(key): str(value) for key, value in secret_literals.items()}
        for secret_key, backend_ref in sorted(secret_refs.items()):
            try:
                raw_value = resolve_secret_reference(
                    secrets_backend,
                    str(backend_ref),
                    default_key=str(secret_key),
                )
            except SecretReferenceError as exc:
                raise FilesystemBindingError(
                    f"filesystem binding {binding.managed_service.kind}/{binding.managed_service.name}#"
                    f"{binding.name} credential reference is invalid or ambiguous",
                ) from exc
            except Exception as exc:
                raise FilesystemBindingError(
                    f"filesystem binding {binding.managed_service.kind}/{binding.managed_service.name}#"
                    f"{binding.name} could not read its credential reference",
                ) from exc
            if raw_value is None:
                raise FilesystemBindingError(
                    f"filesystem binding {binding.managed_service.kind}/{binding.managed_service.name}#"
                    f"{binding.name} references a missing credential",
                )
            if not raw_value:
                raise FilesystemBindingError(
                    f"filesystem binding {binding.managed_service.kind}/{binding.managed_service.name}#"
                    f"{binding.name} credential resolved empty",
                )
            volume_data[str(secret_key)] = raw_value
        resources.append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": binding_secret_name(binding, consumer_key),
                    "namespace": namespace,
                    "labels": {
                        "astrolift.io/managed-by": "astrolift",
                        "astrolift.io/volume-binding": str(binding.guid),
                    },
                },
                "type": "Opaque",
                "stringData": volume_data,
            },
        )
    return resources


def cleanup_binding_resources(
    bindings: Iterable[Any],
    *,
    namespace: str,
    consumer_key: str,
    include_storage: bool = True,
) -> list[dict[str, Any]]:
    """Return deletion stubs only for per-consumer resources Astrolift owns."""

    refs: list[dict[str, Any]] = []
    for binding in bindings:
        if binding.secret_refs or getattr(binding, "secret_literals", None):
            refs.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": binding_secret_name(binding, consumer_key),
                        "namespace": namespace,
                    },
                },
            )
        if include_storage and str(binding.source_kind) == "csi":
            name = binding_resource_name(binding, consumer_key)
            refs.extend(
                [
                    {
                        "apiVersion": "v1",
                        "kind": "PersistentVolumeClaim",
                        "metadata": {"name": name, "namespace": namespace},
                    },
                    {
                        "apiVersion": "v1",
                        "kind": "PersistentVolume",
                        "metadata": {"name": name},
                    },
                ],
            )
    return refs


def agent_volume_bindings(spec: Any) -> list[Any]:
    """Active project filesystem bindings attached to an agent spec."""

    if spec is None or not getattr(spec, "pk", None):
        return []
    try:
        attachments = (
            spec.project_managed_service_attachments.select_related(
                "managed_service__tenant_cluster",
                "managed_service__app_environment__tenant_cluster",
            )
            .prefetch_related("managed_service__volume_bindings")
            .filter(
                deleted_at__isnull=True,
                managed_service__deleted_at__isnull=True,
                managed_service__status__in=["active", "updating"],
                managed_service__kind__in=["filesystem", "nfs"],
            )
            .order_by("managed_service__name", "pk")
        )
    except (AttributeError, TypeError):
        return []
    rows: list[Any] = []
    for attachment in attachments:
        rows.extend(
            attachment.managed_service.volume_bindings.filter(deleted_at__isnull=True).order_by("name"),
        )
    return rows


def binding_resource_name(binding: Any, consumer_key: str) -> str:
    identity = str(binding.guid)
    if str(binding.source_kind) == "dynamic_pvc":
        # Binding rows are replaced when provisioning finalizes. Base a
        # dynamic claim on the durable service identity so a retry/update
        # reconciles the same PVC instead of silently orphaning its data.
        identity = f"{binding.managed_service.guid}:{binding.name}"
    digest = hashlib.sha256(f"{identity}:{consumer_key}".encode()).hexdigest()[:12]
    logical = re.sub(r"[^a-z0-9-]+", "-", str(binding.name).lower()).strip("-") or "volume"
    return f"alft-fs-{logical[:31]}-{digest}"[:63].rstrip("-")


def binding_secret_name(binding: Any, consumer_key: str) -> str:
    return f"{binding_resource_name(binding, consumer_key)[:58]}-auth"


def storage_consumer_key(binding: Any, *, namespace: str, consumer_key: str) -> str:
    """Keep dynamically provisioned storage stable for a consumer namespace."""

    if str(binding.source_kind) == "dynamic_pvc":
        return f"namespace:{namespace}"
    return consumer_key


def render_binding_storage(
    binding: Any,
    *,
    namespace: str,
    consumer_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return platform storage resources, pod volume, and container mount."""

    _validate_persisted_binding(binding)
    effective_consumer_key = storage_consumer_key(
        binding,
        namespace=namespace,
        consumer_key=consumer_key,
    )
    name = binding_resource_name(binding, effective_consumer_key)
    source_kind = str(binding.source_kind)
    pod_volume: dict[str, Any] = {"name": str(binding.name)}
    resources: list[dict[str, Any]] = []

    if source_kind == "existing_pvc":
        if str(binding.claim_namespace) != namespace:
            raise FilesystemBindingError(
                f"filesystem {binding.name!r} claim is in namespace {binding.claim_namespace!r}, "
                f"not consumer namespace {namespace!r}",
            )
        pod_volume["persistentVolumeClaim"] = {
            "claimName": str(binding.claim_name),
            "readOnly": bool(binding.read_only),
        }
    elif source_kind == "csi":
        secret_refs = dict(binding.secret_refs or {})
        secret_literals = dict(getattr(binding, "secret_literals", None) or {})
        csi: dict[str, Any] = {
            "driver": str(binding.csi_driver),
            "volumeHandle": str(binding.volume_handle),
            "volumeAttributes": {str(k): str(v) for k, v in dict(binding.volume_attributes or {}).items()},
            "readOnly": bool(binding.read_only),
        }
        if secret_refs or secret_literals:
            secret_ref = {
                "name": binding_secret_name(binding, consumer_key),
                "namespace": namespace,
            }
            csi["nodeStageSecretRef"] = secret_ref
            csi["nodePublishSecretRef"] = secret_ref
        pv = {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {
                "name": name,
                "labels": _binding_labels(binding, consumer_key),
            },
            "spec": {
                "capacity": {"storage": str(binding.capacity)},
                "accessModes": list(binding.access_modes or ["ReadWriteMany"]),
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "",
                "mountOptions": [str(value) for value in binding.mount_options or []],
                "claimRef": {"name": name, "namespace": namespace},
                "csi": csi,
            },
        }
        pvc = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": _binding_labels(binding, consumer_key),
            },
            "spec": {
                "accessModes": list(binding.access_modes or ["ReadWriteMany"]),
                "resources": {"requests": {"storage": str(binding.capacity)}},
                "storageClassName": "",
                "volumeName": name,
            },
        }
        resources.extend((pv, pvc))
        pod_volume["persistentVolumeClaim"] = {
            "claimName": name,
            "readOnly": bool(binding.read_only),
        }
    elif source_kind == "dynamic_pvc":
        pvc = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": _binding_labels(binding, effective_consumer_key),
            },
            "spec": {
                "accessModes": list(binding.access_modes or ["ReadWriteOnce"]),
                "resources": {"requests": {"storage": str(binding.capacity)}},
                "storageClassName": str(binding.storage_class_name),
                "volumeMode": "Filesystem",
            },
        }
        resources.append(pvc)
        pod_volume["persistentVolumeClaim"] = {
            "claimName": name,
            "readOnly": bool(binding.read_only),
        }
    else:
        raise FilesystemBindingError(f"unsupported filesystem source kind {source_kind!r}")

    mount = {
        "name": str(binding.name),
        "mountPath": str(binding.mount_path),
        "readOnly": bool(binding.read_only),
    }
    if binding.sub_path:
        mount["subPath"] = str(binding.sub_path)
    return resources, pod_volume, mount


def inject_bindings_into_workloads(
    resources: list[dict[str, Any]],
    bindings: Iterable[Any],
    *,
    namespace: str,
    consumer_key: str,
) -> list[dict[str, Any]]:
    """Attach bindings to selected pod templates and add owned PV/PVC rows."""

    out = list(resources)
    for binding in bindings:
        storage_resources, pod_volume, mount = render_binding_storage(
            binding,
            namespace=namespace,
            consumer_key=consumer_key,
        )
        matched = 0
        target_workloads = {str(value) for value in binding.workload_names or []}
        target_containers = {str(value) for value in binding.container_names or []}
        for resource in out:
            pod_spec = _pod_spec(resource)
            if pod_spec is None:
                continue
            workload_name = str((resource.get("metadata") or {}).get("name") or "")
            if target_workloads and workload_name not in target_workloads:
                continue
            _append_unique_named(pod_spec.setdefault("volumes", []), pod_volume, what="volume")
            containers = list(pod_spec.get("containers") or [])
            selected = (
                [container for container in containers if str(container.get("name")) in target_containers]
                if target_containers
                else containers[:1]
            )
            if not selected:
                raise FilesystemBindingError(
                    f"filesystem {binding.name!r} selected no containers in workload {workload_name!r}",
                )
            for container in selected:
                _append_unique_mount(container.setdefault("volumeMounts", []), mount)
            matched += 1
        if not matched:
            targets = ", ".join(sorted(target_workloads)) if target_workloads else "any pod workload"
            raise FilesystemBindingError(f"filesystem {binding.name!r} did not match {targets}")
        out.extend(storage_resources)
    return sorted(
        out, key=lambda row: (str(row.get("kind", "")), str((row.get("metadata") or {}).get("name", "")))
    )


def _validate_persisted_binding(binding: Any) -> None:
    if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", str(binding.name)) or len(str(binding.name)) > 63:
        raise FilesystemBindingError("filesystem binding name must be a 1-63 character Kubernetes DNS label")
    if not str(binding.mount_path).startswith("/"):
        raise FilesystemBindingError(f"filesystem {binding.name!r} mount path must be absolute")
    if binding.sub_path and (
        str(binding.sub_path).startswith("/") or ".." in str(binding.sub_path).split("/")
    ):
        raise FilesystemBindingError(f"filesystem {binding.name!r} sub path is unsafe")
    if not str(binding.protocol):
        raise FilesystemBindingError(f"filesystem {binding.name!r} protocol is required")
    if str(binding.source_kind) == "existing_pvc" and (
        not str(binding.claim_name) or not str(binding.claim_namespace)
    ):
        raise FilesystemBindingError(f"filesystem {binding.name!r} has no complete existing claim locator")
    if str(binding.source_kind) == "csi" and (not str(binding.csi_driver) or not str(binding.volume_handle)):
        raise FilesystemBindingError(f"filesystem {binding.name!r} has an incomplete CSI source")
    if str(binding.source_kind) == "dynamic_pvc":
        if not str(binding.storage_class_name):
            raise FilesystemBindingError(f"filesystem {binding.name!r} has no StorageClass")
        if binding.claim_name or binding.claim_namespace or binding.volume_handle:
            raise FilesystemBindingError(
                f"filesystem {binding.name!r} mixes dynamic and existing volume fields"
            )
        if binding.secret_refs or getattr(binding, "secret_literals", None):
            raise FilesystemBindingError(
                f"filesystem {binding.name!r} dynamic StorageClass credentials must be operator-managed"
            )
    if any(not str(key) or not str(ref) for key, ref in dict(binding.secret_refs or {}).items()):
        raise FilesystemBindingError(f"filesystem {binding.name!r} has an empty credential reference")
    secret_literals = dict(getattr(binding, "secret_literals", None) or {})
    if any(not str(key) or not str(value) for key, value in secret_literals.items()):
        raise FilesystemBindingError(f"filesystem {binding.name!r} has an empty CSI identity literal")
    if dict(binding.secret_refs or {}).keys() & secret_literals.keys():
        raise FilesystemBindingError(f"filesystem {binding.name!r} has conflicting CSI Secret keys")
    if not re.fullmatch(r"[1-9][0-9]*(?:[EPTGMK]i?|m)?", str(binding.capacity)):
        raise FilesystemBindingError(f"filesystem {binding.name!r} has an invalid storage capacity")
    allowed_access_modes = {"ReadWriteOnce", "ReadOnlyMany", "ReadWriteMany", "ReadWriteOncePod"}
    access_modes = list(binding.access_modes or [])
    if not access_modes or any(str(mode) not in allowed_access_modes for mode in access_modes):
        raise FilesystemBindingError(f"filesystem {binding.name!r} has an unsupported access mode")
    if str(binding.source_kind) == "dynamic_pvc" and len(access_modes) != 1:
        raise FilesystemBindingError(
            f"filesystem {binding.name!r} dynamic claim must request exactly one access mode"
        )


def _binding_labels(binding: Any, consumer_key: str) -> dict[str, str]:
    return {
        "astrolift.io/managed-by": "astrolift",
        "astrolift.io/managed-service": str(binding.managed_service.guid),
        "astrolift.io/volume-binding": str(binding.guid),
        "astrolift.io/consumer": hashlib.sha256(consumer_key.encode()).hexdigest()[:16],
    }


def _pod_spec(resource: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(resource.get("kind") or "")
    api_version = str(resource.get("apiVersion") or "")
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
        return ((resource.get("spec") or {}).get("template") or {}).get("spec")
    if kind == "Service" and api_version.startswith("serving.knative.dev/"):
        return ((resource.get("spec") or {}).get("template") or {}).get("spec")
    return None


def _append_unique_named(rows: list[dict[str, Any]], value: dict[str, Any], *, what: str) -> None:
    name = value["name"]
    existing = next((row for row in rows if row.get("name") == name), None)
    if existing is None:
        rows.append(value)
        return
    if existing != value:
        raise FilesystemBindingError(f"managed filesystem {what} {name!r} conflicts with manifest content")


def _append_unique_mount(rows: list[dict[str, Any]], value: dict[str, Any]) -> None:
    path = value["mountPath"]
    conflict = next(
        (row for row in rows if row.get("mountPath") == path and row.get("name") != value["name"]), None
    )
    if conflict is not None:
        raise FilesystemBindingError(
            f"managed filesystem mount path {path!r} conflicts with volume {conflict.get('name')!r}",
        )
    _append_unique_named(rows, value, what="volume mount")
