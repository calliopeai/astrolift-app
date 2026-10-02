"""Freeze reviewed shared-service placement through each worker effect (#2207).

Legacy histories omit the optional binding. Reviewed work locks its accepted
owners and compares a fixed-size digest before invoking any lifecycle body.
Status, applied config, handles and service versions advance during the worker's
own retries; desired placement/config and parent versions must remain unchanged.
"""

from __future__ import annotations

import hashlib
import json

from django.db import transaction
from temporalio.exceptions import ApplicationError

from astrolift_workflows.inputs import ReviewedManagedServiceBinding


def _row(service_id):
    from astrolift_services.models import ManagedService

    return ManagedService.all_objects.select_related(
        "project__organization", "project__team", "tenant_cluster__provider_plugin"
    ).get(pk=service_id)


def _digest(service):
    project = service.project
    cluster = service.tenant_cluster
    plugin = cluster.provider_plugin
    data = [
        str(service.guid),
        service.name,
        service.kind,
        service.variant,
        service.environment_name,
        service.project_id,
        service.registered_app_id,
        service.app_environment_id,
        service.organization_id,
        service.tenant_cluster_id,
        service.config,
        str(project.guid),
        project.version,
        project.organization_id,
        project.team_id,
        str(project.organization.guid),
        project.organization.version,
        str(project.team.guid),
        project.team.version,
        str(cluster.guid),
        cluster.version,
        cluster.organization_id,
        cluster.provider_plugin_id,
        cluster.provider_config,
        str(plugin.guid),
        plugin.version,
        service.operation_kind,
        service.operation_workflow_id,
        service.operation_started_at.isoformat() if service.operation_started_at else None,
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def capture_reviewed_binding(service_id: int) -> ReviewedManagedServiceBinding:
    row = _row(service_id)
    return ReviewedManagedServiceBinding(
        service_guid=str(row.guid),
        organization_id=row.project.organization_id,
        project_id=row.project_id,
        team_id=row.project.team_id,
        cluster_id=row.tenant_cluster_id,
        provider_plugin_id=row.tenant_cluster.provider_plugin_id,
        accepted_service_version=row.version,
        digest=_digest(row),
    )


def reviewed_service_call(function, service_id: int, binding: ReviewedManagedServiceBinding | None, *args):
    if binding is None:
        return function(service_id, *args)
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_services.models import ManagedService

    def refuse():
        raise ApplicationError(
            "Reviewed managed resource context changed; provider work refused",
            type="STALE_TARGET",
            non_retryable=True,
        )

    with transaction.atomic():
        # Parent-first ordering matches admission. Locks stay held while the
        # existing synchronous lifecycle body calls a provider or writes rows.
        parents = [
            Organization.objects.select_for_update(no_key=True).filter(pk=binding.organization_id).first(),
            Team.objects.select_for_update(no_key=True)
            .filter(pk=binding.team_id, organization_id=binding.organization_id)
            .first(),
            Project.objects.select_for_update(no_key=True)
            .filter(pk=binding.project_id, organization_id=binding.organization_id, team_id=binding.team_id)
            .first(),
            TenantCluster.objects.select_for_update(no_key=True).filter(pk=binding.cluster_id).first(),
            ProviderPlugin.objects.select_for_update(no_key=True)
            .filter(pk=binding.provider_plugin_id)
            .first(),
        ]
        row = (
            ManagedService.all_objects.select_for_update(of=("self",))
            .filter(
                pk=service_id,
                guid=binding.service_guid,
                project_id=binding.project_id,
                tenant_cluster_id=binding.cluster_id,
                registered_app__isnull=True,
                app_environment__isnull=True,
                organization__isnull=True,
            )
            .first()
        )
        if row is None or any(parent is None for parent in parents):
            refuse()
        current = _row(service_id)
        if (
            current.tenant_cluster.provider_plugin_id != binding.provider_plugin_id
            or _digest(current) != binding.digest
            or current.deleted_at is not None
            and function.__name__ != "_finalize_sync"
        ):
            refuse()
        return function(service_id, *args)
