"""
NormalizedManifest → DB rows (RegisteredApp / Workload / Container).

Idempotent diff-and-apply layer between the parser and the runtime
data model. The contract:

* A new manifest creates the rows it needs and writes the
  serialized JSON + SHA-256 hash onto ``RegisteredApp.manifest_normalized``
  and ``RegisteredApp.manifest_hash``.
* An unchanged manifest is a no-op (no version bumps on rows whose
  content didn't change). The hash on RegisteredApp is the cheap
  short-circuit for the common 'no change' path.
* A modified manifest creates/updates the rows that changed and
  soft-deletes the rows that disappeared from the manifest.

What this module does NOT do (deliberately):

* Persist ManagedService / AppEnvironment rows. Both are tracked as
  separate models with their own provisioning lifecycles; binding
  them to manifest rows lands in a follow-up that needs the driver
  protocol.
* Apply the rendered Kubernetes resources. That's
  ``apply_manifests`` in the deploy workflow.
"""

from __future__ import annotations

import dataclasses

from astrolift_manifest.normalize import manifest_hash
from astrolift_manifest.types import (
    ContainerManifest,
    NormalizedManifest,
    WorkloadManifest,
)


@dataclasses.dataclass(slots=True)
class PersistResult:
    """Summary of what changed. Useful for audit logs and as a
    cheap signal to skip downstream sync work when nothing changed."""

    workloads_created: int = 0
    workloads_updated: int = 0
    workloads_soft_deleted: int = 0
    containers_created: int = 0
    containers_updated: int = 0
    containers_soft_deleted: int = 0
    hash_changed: bool = False

    @property
    def changed(self) -> bool:
        return (
            self.hash_changed
            or self.workloads_created
            or self.workloads_updated
            or self.workloads_soft_deleted
            or self.containers_created
            or self.containers_updated
            or self.containers_soft_deleted
        ) > 0


def persist_manifest(app, manifest: NormalizedManifest, *, raw_text: str = "") -> PersistResult:
    """Reconcile ``app``'s Workload + Container rows against ``manifest``.

    Pass the original TOML text via ``raw_text`` so it round-trips
    onto ``RegisteredApp.manifest_raw`` (caller may already have it
    handy). If omitted, the field is left unchanged.
    """
    from astrolift_registry.models import Workload

    new_hash = manifest_hash(manifest.serialized)
    result = PersistResult(hash_changed=app.manifest_hash != new_hash)

    fields_to_update: list[str] = []
    if app.manifest_hash != new_hash:
        app.manifest_hash = new_hash
        fields_to_update.append("manifest_hash")
    if app.manifest_normalized != manifest.serialized:
        app.manifest_normalized = manifest.serialized
        fields_to_update.append("manifest_normalized")
    if raw_text and app.manifest_raw != raw_text:
        app.manifest_raw = raw_text
        fields_to_update.append("manifest_raw")
    if fields_to_update:
        fields_to_update += ["updated_at", "version"]
        app.save(update_fields=fields_to_update)

    desired_by_slug: dict[str, WorkloadManifest] = {w.name: w for w in manifest.workloads}
    existing = {
        w.slug: w
        for w in Workload.objects.filter(registered_app=app, deleted_at__isnull=True).select_related(
            "registered_app"
        )
    }

    # Soft-delete workloads that disappeared from the manifest.
    for slug, row in existing.items():
        if slug not in desired_by_slug:
            row.soft_delete()
            result.workloads_soft_deleted += 1

    # Upsert the rest.
    for slug, m in desired_by_slug.items():
        row = existing.get(slug)
        if row is None:
            row = Workload.objects.create(
                registered_app=app,
                name=m.name,
                slug=slug,
                kind=m.kind,
                is_public=m.is_public,
                schedule=m.schedule or "",
                concurrency_policy=m.concurrency_policy or "forbid",
                replicas=m.replicas,
                cpu_request=m.cpu_request or "",
                cpu_limit=m.cpu_limit or "",
                memory_request=m.memory_request or "",
                memory_limit=m.memory_limit or "",
                hpa_min_replicas=m.hpa_min,
                hpa_max_replicas=m.hpa_max,
                hpa_target_cpu_pct=m.hpa_target_cpu_pct,
                storage_class=m.storage_class or "",
                storage_size=m.storage_size or "",
                volumes=list(m.volumes),
                max_retries=m.max_retries,
                tool_timeout_seconds=m.tool_timeout_seconds,
                result_ttl_hours=m.result_ttl_hours,
            )
            result.workloads_created += 1
        elif _workload_changed(row, m):
            row.kind = m.kind
            row.is_public = m.is_public
            row.schedule = m.schedule or ""
            row.concurrency_policy = m.concurrency_policy or "forbid"
            row.replicas = m.replicas
            row.cpu_request = m.cpu_request or ""
            row.cpu_limit = m.cpu_limit or ""
            row.memory_request = m.memory_request or ""
            row.memory_limit = m.memory_limit or ""
            row.hpa_min_replicas = m.hpa_min
            row.hpa_max_replicas = m.hpa_max
            row.hpa_target_cpu_pct = m.hpa_target_cpu_pct
            row.storage_class = m.storage_class or ""
            row.storage_size = m.storage_size or ""
            row.volumes = list(m.volumes)
            row.max_retries = m.max_retries
            row.tool_timeout_seconds = m.tool_timeout_seconds
            row.result_ttl_hours = m.result_ttl_hours
            row.save()
            result.workloads_updated += 1

        # Containers diff under the workload row.
        c_result = _persist_containers(row, m.containers)
        result.containers_created += c_result.created
        result.containers_updated += c_result.updated
        result.containers_soft_deleted += c_result.soft_deleted

    return result


@dataclasses.dataclass(slots=True)
class _ContainerDiff:
    created: int = 0
    updated: int = 0
    soft_deleted: int = 0


def _persist_containers(workload, containers: tuple[ContainerManifest, ...]) -> _ContainerDiff:
    from astrolift_registry.models import Container

    desired_by_name = {c.name: c for c in containers}
    existing = {c.name: c for c in Container.objects.filter(workload=workload, deleted_at__isnull=True)}

    diff = _ContainerDiff()
    for name, row in existing.items():
        if name not in desired_by_name:
            row.soft_delete()
            diff.soft_deleted += 1

    for name, m in desired_by_name.items():
        env_dict = dict(m.env)
        row = existing.get(name)
        if row is None:
            Container.objects.create(
                workload=workload,
                name=m.name,
                is_primary=m.is_primary,
                image_ref=m.image_ref or "",
                dockerfile_path=m.dockerfile_path,
                build_context=m.build_context,
                port=m.port,
                command=list(m.command),
                args=list(m.args),
                env=env_dict,
                healthcheck_kind=m.healthcheck_kind,
                healthcheck_value=m.healthcheck_value or "",
                healthcheck_port=m.healthcheck_port,
            )
            diff.created += 1
        elif _container_changed(row, m, env_dict):
            row.is_primary = m.is_primary
            row.image_ref = m.image_ref or ""
            row.dockerfile_path = m.dockerfile_path
            row.build_context = m.build_context
            row.port = m.port
            row.command = list(m.command)
            row.args = list(m.args)
            row.env = env_dict
            row.healthcheck_kind = m.healthcheck_kind
            row.healthcheck_value = m.healthcheck_value or ""
            row.healthcheck_port = m.healthcheck_port
            row.save()
            diff.updated += 1

    return diff


def _workload_changed(row, m: WorkloadManifest) -> bool:
    return (
        row.kind != m.kind
        or row.is_public != m.is_public
        or (row.schedule or "") != (m.schedule or "")
        or (row.concurrency_policy or "forbid") != (m.concurrency_policy or "forbid")
        or row.replicas != m.replicas
        or (row.cpu_request or "") != (m.cpu_request or "")
        or (row.cpu_limit or "") != (m.cpu_limit or "")
        or (row.memory_request or "") != (m.memory_request or "")
        or (row.memory_limit or "") != (m.memory_limit or "")
        or row.hpa_min_replicas != m.hpa_min
        or row.hpa_max_replicas != m.hpa_max
        or row.hpa_target_cpu_pct != m.hpa_target_cpu_pct
        or (row.storage_class or "") != (m.storage_class or "")
        or (row.storage_size or "") != (m.storage_size or "")
        or list(row.volumes or []) != list(m.volumes)
        or row.max_retries != m.max_retries
        or row.tool_timeout_seconds != m.tool_timeout_seconds
        or row.result_ttl_hours != m.result_ttl_hours
    )


def _container_changed(row, m: ContainerManifest, env_dict: dict) -> bool:
    return (
        row.is_primary != m.is_primary
        or (row.image_ref or "") != (m.image_ref or "")
        or row.dockerfile_path != m.dockerfile_path
        or row.build_context != m.build_context
        or row.port != m.port
        or list(row.command or []) != list(m.command)
        or list(row.args or []) != list(m.args)
        or (row.env or {}) != env_dict
        or row.healthcheck_kind != m.healthcheck_kind
        or (row.healthcheck_value or "") != (m.healthcheck_value or "")
        or row.healthcheck_port != m.healthcheck_port
    )
