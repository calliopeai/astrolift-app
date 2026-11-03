"""Database side of the managed-service match (#2606).

Reads the stored manifest (``RegisteredApp.manifest_raw``) and the platform
rows, then hands both to the pure ``plan_managed_service_match``. The declared
side reuses the reconcile's own normalization (``_merge_config``,
``_lifecycle_policy``, variant resolution), so a plan and the apply that
follows agree on what "different" means.
"""

from __future__ import annotations

from astrolift_manifest.managed_service_match import (
    APP,
    PROJECT,
    PlannedChange,
    RecordedService,
    ServiceSpec,
    plan_managed_service_match,
)


def _row_spec(row, *, owner_scope: str, environment: str, bind_workloads) -> ServiceSpec:
    return ServiceSpec(
        owner_scope=owner_scope,
        environment=environment,
        kind=row.kind,
        name=row.name,
        variant=row.variant or "",
        isolation=row.isolation or "",
        config=dict(row.config or {}),
        bind_workloads=tuple(bind_workloads),
        deletion_policy=row.deletion_policy,
        lifecycle_policy=dict(row.lifecycle_policy or {}),
    )


def declared_specs(app) -> tuple[ServiceSpec, ...]:
    """The app's stored ``[[managed_services]]`` as reconcile would apply them.

    Raises ``ManifestError`` for an unparseable stored manifest and
    ``ValueError`` for a declaration the reconcile would refuse.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_manifest.normalize import normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_manifest.persist import _lifecycle_policy, _merge_config
    from astrolift_services.managed_service_catalog import resolve_variant, validate_config

    if not app.manifest_raw:
        return ()
    services = normalize(parse_raw(app.manifest_raw)).managed_services
    environments = {
        row.name: row
        for row in AppEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        ).select_related("tenant_cluster__provider_plugin")
    }
    specs: list[ServiceSpec] = []
    for service in services:
        name = (service.name or service.kind).strip()
        if service.owner_scope == "project" and app.project_id is None:
            raise ValueError(
                f"managed service {name!r} has owner_scope 'project' but the app belongs to no project"
            )
        env = environments.get(service.environment)
        if env is None:
            raise ValueError(f"managed service {name!r} selects unknown environment {service.environment!r}")
        config = _merge_config(service)
        item = resolve_variant(
            plugin_slug=env.tenant_cluster.provider_plugin.slug,
            kind=service.kind,
            requested_variant=service.variant,
        )
        if item is not None:
            validate_config(item, config)
        variant = item.variant if item is not None else (service.variant or "")
        specs.append(
            ServiceSpec(
                owner_scope=service.owner_scope,
                environment=service.environment,
                kind=service.kind,
                name=name,
                variant=variant,
                isolation=service.isolation,
                config=config,
                bind_workloads=tuple(service.bind_workloads),
                deletion_policy=service.deletion_policy,
                lifecycle_policy=_lifecycle_policy(service),
            )
        )
    return tuple(specs)


def recorded_services(app) -> tuple[tuple[RecordedService, ...], dict[tuple[str, str], ServiceSpec]]:
    """The app's platform rows and attachments, and the shared project rows."""
    from astrolift_manifest.persist import _needs_provision_retry
    from astrolift_services.models import ManagedService, ManagedServiceAttachment

    recorded: list[RecordedService] = []
    for row in ManagedService.objects.filter(registered_app=app, deleted_at__isnull=True).select_related(
        "app_environment"
    ):
        if row.app_environment is None:
            continue
        recorded.append(
            RecordedService(
                spec=_row_spec(
                    row,
                    owner_scope=APP,
                    environment=row.app_environment.name,
                    bind_workloads=row.bind_workloads or (),
                ),
                manifest_managed=row.manifest_managed,
                backend_ref=row.backend_ref or "",
                needs_retry=_needs_provision_retry(row),
            )
        )

    project_rows: dict[tuple[str, str], ServiceSpec] = {}
    if app.project_id is not None:
        for row in ManagedService.objects.filter(project_id=app.project_id, deleted_at__isnull=True):
            project_rows[(row.kind, row.name)] = _row_spec(
                row, owner_scope=PROJECT, environment=row.environment_name, bind_workloads=()
            )
        attachments = ManagedServiceAttachment.objects.filter(
            app_environment__registered_app=app,
            deleted_at__isnull=True,
            managed_service__deleted_at__isnull=True,
        ).select_related("managed_service", "app_environment")
        for attachment in attachments:
            row = attachment.managed_service
            recorded.append(
                RecordedService(
                    spec=_row_spec(
                        row,
                        owner_scope=PROJECT,
                        environment=attachment.app_environment.name,
                        bind_workloads=attachment.workload_names or (),
                    ),
                    manifest_managed=attachment.manifest_managed,
                    backend_ref=row.backend_ref or "",
                    needs_retry=_needs_provision_retry(row),
                )
            )
    return tuple(recorded), project_rows


def build_match_plan(app) -> tuple[tuple[ServiceSpec, ...], tuple[PlannedChange, ...]]:
    """(declared specs, planned changes) for ``app``. Read-only."""
    declared = declared_specs(app)
    recorded, project_rows = recorded_services(app)
    return declared, plan_managed_service_match(declared, recorded, project_rows)
