"""Stamp persisted identities on controllers and their future pods."""

from astrolift_registry.models import Workload
from providers._sdk.workload_metrics import APP_ID_LABEL, ENVIRONMENT_ID_LABEL, WORKLOAD_ID_LABEL


def stamp_metric_identities(resources, *, app, environment):
    if environment.registered_app_id != app.pk:
        raise ValueError("runtime environment does not belong to app")
    workloads = {
        row.slug: str(row.guid)
        for row in Workload.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True).only(
            "slug", "guid"
        )
    }
    for resource in resources:
        metadata = resource.get("metadata") or {}
        workload_slug = (metadata.get("labels") or {}).get("astrolift.dev/workload")
        if workload_slug not in workloads:
            continue
        identities = {
            APP_ID_LABEL: str(app.guid),
            ENVIRONMENT_ID_LABEL: str(environment.guid),
            WORKLOAD_ID_LABEL: workloads[workload_slug],
        }
        metadata.setdefault("labels", {}).update(identities)
        spec = resource.get("spec") or {}
        if resource.get("kind") == "CronJob":
            template = spec.get("jobTemplate") or {}
            template.setdefault("metadata", {}).setdefault("labels", {}).update(identities)
            spec = template.get("spec") or {}
        template = spec.get("template")
        if isinstance(template, dict):
            template.setdefault("metadata", {}).setdefault("labels", {}).update(identities)
