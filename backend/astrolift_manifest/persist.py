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

* Create AppEnvironment rows. Registration owns cluster selection and
  environment bootstrap. Managed-service reconciliation runs immediately when
  those rows already exist and is repeated by the bootstrap helper after it
  creates them.
* Apply the rendered Kubernetes resources. That's
  ``apply_manifests`` in the deploy workflow.
"""

from __future__ import annotations

import dataclasses

from astrolift_manifest.normalize import manifest_hash
from astrolift_manifest.types import (
    ContainerManifest,
    ManagedServiceManifest,
    NormalizedManifest,
    WorkloadManifest,
)

# Reconcile actions that only take a binding away: nothing new can read a
# service's credentials through one (applyStagedManifest's approval rule).
RELEASE_ACTIONS = frozenset({"remove", "detach"})


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
    managed_services_created: int = 0
    managed_services_updated: int = 0
    managed_services_removed: int = 0
    managed_service_attachments_created: int = 0
    managed_service_attachments_updated: int = 0
    managed_service_attachments_removed: int = 0
    # One (action, target) pair per managed-service row or attachment the
    # reconcile created, changed or released, e.g. ("attach",
    # "project:postgres/shared@production"). Names only, so a caller can
    # gate or audit exactly what changed (applyStagedManifest, #1759)
    # without re-deriving reconcile's rules; RELEASE_ACTIONS are the ones
    # that only take a binding away.
    managed_service_changes: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    # Declarations the reconcile could not act on yet because the app has no
    # environment. Registration's environment bootstrap reconciles them
    # later with no caller to check, so a caller that gates bindings has to
    # gate these now (applyStagedManifest, #1759).
    managed_services_deferred: tuple[ManagedServiceManifest, ...] = ()
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
            or self.managed_services_created
            or self.managed_services_updated
            or self.managed_services_removed
            or self.managed_service_attachments_created
            or self.managed_service_attachments_updated
            or self.managed_service_attachments_removed
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

    managed = reconcile_managed_services(app, manifest.managed_services)
    result.managed_services_created += managed.managed_services_created
    result.managed_services_updated += managed.managed_services_updated
    result.managed_services_removed += managed.managed_services_removed
    result.managed_service_attachments_created += managed.managed_service_attachments_created
    result.managed_service_attachments_updated += managed.managed_service_attachments_updated
    result.managed_service_attachments_removed += managed.managed_service_attachments_removed
    result.managed_service_changes += managed.managed_service_changes
    result.managed_services_deferred = managed.managed_services_deferred

    return result


def _merge_config(service: ManagedServiceManifest) -> dict:
    """Materialize common and provider extension fields without collisions."""
    merged = dict(service.config)
    common = {"size": service.size}
    if service.auth_mode:
        common["auth_mode"] = service.auth_mode
    for group in (common, service.networking, service.retention, service.backup, service.extensions):
        for key, value in group.items():
            if key in merged and merged[key] != value:
                raise ValueError(
                    f"managed service {service.name or service.kind!r} config field {key!r} "
                    "is declared twice with different values"
                )
            merged[key] = value
    return merged


def _lifecycle_policy(service: ManagedServiceManifest) -> dict:
    return {
        "retention": dict(service.retention),
        "backup": dict(service.backup),
        "restore": dict(service.restore),
        "confirm_delete": service.confirm_delete,
    }


def _enqueue_manifest_workflow(name: str, row, input_value) -> None:
    """Start after commit; a queue outage must not roll back desired state."""
    import logging

    from django.db import transaction

    def start() -> None:
        try:
            from astrolift_workflows.client import start_workflow

            start_workflow(
                name,
                args=[input_value],
                workflow_id=f"{name}-{row.guid}",
            )
        except Exception:  # noqa: BLE001 - desired state remains retryable
            logging.getLogger(__name__).exception(
                "manifest: failed to enqueue %s for managed service %s",
                name,
                row.guid,
            )

    transaction.on_commit(start)


def _enqueue_provision(row) -> None:
    from astrolift_workflows.inputs import Actor, ProvisionManagedServiceInput

    _enqueue_manifest_workflow(
        "ProvisionManagedServiceWorkflow",
        row,
        ProvisionManagedServiceInput(
            managed_service_id=row.pk,
            actor=Actor(kind="system", display="manifest-reconcile"),
        ),
    )


def _enqueue_update(row) -> None:
    from astrolift_workflows.inputs import Actor, UpdateManagedServiceInput

    _enqueue_manifest_workflow(
        "UpdateManagedServiceWorkflow",
        row,
        UpdateManagedServiceInput(
            managed_service_id=row.pk,
            actor=Actor(kind="system", display="manifest-reconcile"),
        ),
    )


def _enqueue_deprovision(row) -> None:
    from astrolift_workflows.inputs import Actor, DeprovisionManagedServiceInput

    lifecycle = dict(row.lifecycle_policy or {})
    delete_data = row.deletion_policy == "delete" and lifecycle.get("confirm_delete") is True
    _enqueue_manifest_workflow(
        "DeprovisionManagedServiceWorkflow",
        row,
        DeprovisionManagedServiceInput(
            managed_service_id=row.pk,
            actor=Actor(kind="system", display="manifest-reconcile"),
            delete_data=delete_data,
            force_destroy=False,
        ),
    )


def _needs_provision_retry(row) -> bool:
    """Has this row been declared but never actually provisioned?

    ``_enqueue_manifest_workflow`` swallows a start failure on purpose --
    "desired state remains retryable" -- but nothing retried it (#1688).
    Enqueueing happened only when the row was created or its manifest
    block changed, so a first enqueue lost to a queue outage, a stopped
    worker or an unreachable Temporal was lost for good: every later
    deploy re-reconciled, saw no change, and enqueued nothing. The row
    kept ``backend_ref=""`` forever, no driver call was ever attempted,
    and the app's binding secret stayed empty -- which reads as "the
    platform ignored my manifest", because in effect it did.

    ``PROVISIONING`` and ``DEPROVISIONING`` are excluded: a run is
    already in flight, and re-enqueueing would terminate and restart it
    on every deploy (``start_workflow`` reuses the id under
    TERMINATE_IF_RUNNING). ``FAILED`` is included -- a failed provision
    should be retried by the next deploy, which is the operator's normal
    "fix it and push" loop.
    """

    from astrolift_services.models import ManagedService

    if row.backend_ref:
        return False
    return row.status not in (
        ManagedService.Status.PROVISIONING,
        ManagedService.Status.DEPROVISIONING,
    )


def reconcile_managed_services(app, services: tuple[ManagedServiceManifest, ...]) -> PersistResult:
    """Reconcile manifest declarations into real lifecycle-owned rows.

    App scope produces one resource per selected environment. Project scope
    produces one shared project resource and one attachment per environment.
    When environments do not exist yet registration calls this again after
    bootstrap, so nothing is reconciled here; the declarations come back in
    ``managed_services_deferred`` instead, for a caller that gates them.
    """
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.managed_service_catalog import resolve_variant, validate_config
    from astrolift_services.models import ManagedService, ManagedServiceAttachment
    from astrolift_services.secret_ref_config import assert_config_secret_refs_scoped

    result = PersistResult()
    # Before any lookup: filtering project services on project=None matches
    # every app-private row of that kind and name, in any org, and creating
    # one with no owner trips the owner-scope CHECK constraint.
    if app.project_id is None:
        for service in services:
            if service.owner_scope == "project":
                raise ValueError(
                    f"managed service {service.name or service.kind!r} has owner_scope 'project' "
                    "but the app belongs to no project"
                )
    environments = {
        row.name: row
        for row in AppEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        ).select_related("tenant_cluster__provider_plugin")
    }
    if not environments:
        result.managed_services_deferred = tuple(services)
        return result

    desired_app: set[tuple[int, str, str]] = set()
    desired_attachments: set[int] = set()
    project_rows: dict[tuple[str, str], ManagedService] = {}

    for service in services:
        env = environments.get(service.environment)
        if env is None:
            raise ValueError(
                f"managed service {service.name or service.kind!r} selects unknown environment "
                f"{service.environment!r}"
            )
        config = _merge_config(service)
        item = resolve_variant(
            plugin_slug=env.tenant_cluster.provider_plugin.slug,
            kind=service.kind,
            requested_variant=service.variant,
        )
        if item is not None:
            validate_config(item, config)
        # The service's owner decides its secret namespace (#1921): the app
        # for an app-scoped service, the project for a project-scoped one.
        owner = app if service.owner_scope == "app" else app.project
        try:
            assert_config_secret_refs_scoped(config, owner=owner, cluster=env.tenant_cluster)
        except SecretRefNamespaceError as exc:
            raise ValueError(f"managed service {service.name or service.kind!r}: {exc}") from exc
        variant = item.variant if item is not None else (service.variant or "")
        name = (service.name or service.kind).strip()
        lifecycle = _lifecycle_policy(service)

        if service.owner_scope == "app":
            key = (env.pk, service.kind, name)
            desired_app.add(key)
            row = ManagedService.objects.filter(
                registered_app=app,
                app_environment=env,
                kind=service.kind,
                name=name,
                deleted_at__isnull=True,
            ).first()
            if row is None:
                row = ManagedService.objects.create(
                    registered_app=app,
                    app_environment=env,
                    kind=service.kind,
                    name=name,
                    variant=variant,
                    isolation=service.isolation,
                    config=config,
                    manifest_managed=True,
                    bind_workloads=list(service.bind_workloads),
                    deletion_policy=service.deletion_policy,
                    lifecycle_policy=lifecycle,
                )
                result.managed_services_created += 1
                result.managed_service_changes.append(("create", f"app:{service.kind}/{name}@{env.name}"))
                _enqueue_provision(row)
                continue
            if not row.manifest_managed:
                raise ValueError(
                    f"managed service ({service.kind}, {name!r}) already exists imperatively in "
                    f"environment {env.name!r}; the manifest cannot take ownership"
                )
            changed = any(
                (
                    row.variant != variant,
                    row.isolation != service.isolation,
                    row.config != config,
                    row.bind_workloads != list(service.bind_workloads),
                    row.deletion_policy != service.deletion_policy,
                    row.lifecycle_policy != lifecycle,
                )
            )
            if changed:
                variant_changed = row.variant != variant or row.isolation != service.isolation
                if variant_changed and row.backend_ref:
                    raise ValueError(
                        f"managed service {name!r} changes variant or isolation; remove it, let safe "
                        "deprovision finish, then add the replacement"
                    )
                row.variant = variant
                row.isolation = service.isolation
                row.config = config
                row.bind_workloads = list(service.bind_workloads)
                row.deletion_policy = service.deletion_policy
                row.lifecycle_policy = lifecycle
                row.save()
                result.managed_services_updated += 1
                result.managed_service_changes.append(("update", f"app:{service.kind}/{name}@{env.name}"))
                _enqueue_update(row) if row.backend_ref else _enqueue_provision(row)
            elif _needs_provision_retry(row):
                # Unchanged, but never provisioned -- the enqueue that
                # should have happened at create time never landed (#1688).
                _enqueue_provision(row)
            continue

        project_key = (service.kind, name)
        row = project_rows.get(project_key)
        if row is None:
            row = ManagedService.objects.filter(
                project=app.project,
                kind=service.kind,
                name=name,
                deleted_at__isnull=True,
            ).first()
            if row is None:
                row = ManagedService.objects.create(
                    project=app.project,
                    tenant_cluster=env.tenant_cluster,
                    environment_name=service.environment,
                    kind=service.kind,
                    name=name,
                    variant=variant,
                    isolation=service.isolation,
                    config=config,
                    manifest_managed=True,
                    deletion_policy=service.deletion_policy,
                    lifecycle_policy=lifecycle,
                )
                result.managed_services_created += 1
                result.managed_service_changes.append(("create", f"project:{service.kind}/{name}"))
                _enqueue_provision(row)
            else:
                if row.tenant_cluster_id != env.tenant_cluster_id:
                    raise ValueError(f"project managed service {name!r} already belongs to another cluster")
                if row.variant != variant or row.config != config or row.isolation != service.isolation:
                    raise ValueError(
                        f"project managed service {name!r} already exists with different desired state; "
                        "shared consumers cannot overwrite it from an app manifest"
                    )
                if _needs_provision_retry(row):
                    # Same lost-enqueue gap as the app-scoped branch (#1688).
                    _enqueue_provision(row)
            project_rows[project_key] = row

        attachment = ManagedServiceAttachment.objects.filter(
            managed_service=row,
            app_environment=env,
            deleted_at__isnull=True,
        ).first()
        if attachment is None:
            attachment = ManagedServiceAttachment.objects.create(
                managed_service=row,
                app_environment=env,
                manifest_managed=True,
                workload_names=list(service.bind_workloads),
            )
            result.managed_service_attachments_created += 1
            result.managed_service_changes.append(("attach", f"project:{service.kind}/{name}@{env.name}"))
        elif attachment.manifest_managed and attachment.workload_names != list(service.bind_workloads):
            attachment.workload_names = list(service.bind_workloads)
            attachment.save(update_fields=["workload_names", "updated_at", "version"])
            result.managed_service_attachments_updated += 1
            result.managed_service_changes.append(("rebind", f"project:{service.kind}/{name}@{env.name}"))
        elif not attachment.manifest_managed:
            raise ValueError(
                f"project managed service {name!r} is already attached imperatively to {env.name!r}"
            )
        desired_attachments.add(attachment.pk)

    for row in ManagedService.objects.filter(
        registered_app=app,
        manifest_managed=True,
        deleted_at__isnull=True,
    ):
        if (row.app_environment_id, row.kind, row.name) in desired_app:
            continue
        row.manifest_managed = False
        row.save(update_fields=["manifest_managed", "updated_at", "version"])
        result.managed_services_removed += 1
        env_name = row.app_environment.name if row.app_environment_id else ""
        result.managed_service_changes.append(("remove", f"app:{row.kind}/{row.name}@{env_name}"))
        _enqueue_deprovision(row)

    stale_attachments = ManagedServiceAttachment.objects.filter(
        app_environment__registered_app=app,
        manifest_managed=True,
        deleted_at__isnull=True,
    ).exclude(pk__in=desired_attachments)
    for attachment in stale_attachments.select_related("managed_service", "app_environment"):
        attachment.soft_delete()
        result.managed_service_attachments_removed += 1
        service_row = attachment.managed_service
        result.managed_service_changes.append(
            ("detach", f"project:{service_row.kind}/{service_row.name}@{attachment.app_environment.name}")
        )

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
