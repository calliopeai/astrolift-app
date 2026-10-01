"""Current owner/locator admission and durable preview slice binding reconstruction."""

from dataclasses import replace

from _sdk.managed_service import ServiceHandle, SliceSpec
from django.db.models import Q
from k8s_native.managed._handle import unpack


def live_slice_source(service, preview_env):
    from astrolift_clusters.models import TenantCluster
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_services.models import ManagedService

    app_id = preview_env.registered_app_id
    org_id = preview_env.registered_app.organization_id
    org = Organization.objects.select_for_update().filter(pk=org_id).first()
    app = RegisteredApp.objects.select_for_update().filter(pk=app_id, organization_id=org_id).first()
    if org is None or app is None:
        raise ValueError("preview slice owner is unavailable")
    if app.team_id:
        team = Team.objects.select_for_update().filter(pk=app.team_id, organization_id=org.pk).first()
        if team is None:
            raise ValueError("preview slice team owner is unavailable")
    if app.project_id:
        project = (
            Project.objects.select_for_update().filter(pk=app.project_id, organization_id=org.pk).first()
        )
        if project is None or (app.team_id and project.team_id != app.team_id):
            raise ValueError("preview slice project ancestry is unavailable")
        if (
            Team.objects.select_for_update().filter(pk=project.team_id, organization_id=org.pk).first()
            is None
        ):
            raise ValueError("preview slice project team is unavailable")
    preview = AppEnvironment.objects.select_for_update().filter(pk=preview_env.pk, registered_app=app).first()
    if preview is None or preview.previewed_environment_id is None:
        raise ValueError("preview slice consumer has no current source")
    primary = (
        AppEnvironment.objects.select_for_update()
        .filter(pk=preview.previewed_environment_id, registered_app=app)
        .first()
    )
    if (
        primary is None
        or preview.tenant_cluster_id is None
        or primary.tenant_cluster_id != preview.tenant_cluster_id
    ):
        raise ValueError("preview slice source and consumer must use the same live cluster")
    cluster = (
        TenantCluster.objects.select_for_update()
        .filter(pk=primary.tenant_cluster_id)
        .exclude(lifecycle__in=["decommissioning", "decommissioned"])
        .filter(Q(organization_id=org.pk) | Q(organization_id__isnull=True))
        .first()
    )
    if cluster is None:
        raise ValueError("preview slice cluster is unavailable")
    current = (
        ManagedService.objects.select_for_update()
        .filter(pk=service.pk, registered_app=app, app_environment=primary, status="active")
        .first()
    )
    if current is None:
        raise ValueError("preview slice service is unavailable or reassigned")
    driver = slice_driver_registration(current, cluster).driver_cls()
    if getattr(driver, "supports_slicing", lambda: False)():
        parsed = unpack(current.backend_ref)
        if parsed.is_legacy or parsed.cluster_id != str(cluster.guid):
            raise ValueError("preview slice handle does not name the current source cluster")
    spec = SliceSpec(
        slice_id=str(preview.guid),
        parent=ServiceHandle(current.backend_ref, managed_service_id=str(current.guid)),
        organization_id=str(org.guid),
        app_id=str(app.guid),
        environment_id=str(preview.guid),
        labels={"astrolift.io/organization": org.slug, "astrolift.io/app": app.slug},
    )
    return current, preview, cluster, spec


def slice_driver_registration(service, cluster):
    from astrolift_drivers.managed_resolution import resolve_managed_driver

    return resolve_managed_driver(
        cluster_plugin_slug=cluster.provider_plugin.slug, kind=service.kind, variant=service.variant or ""
    )


def configured_slice_driver(service, cluster, *, credentials=False):
    from core.cluster_observability import managed_config_for

    resolved = slice_driver_registration(service, cluster)
    if not getattr(resolved.driver_cls(), "supports_slicing", lambda: False)():
        return None
    cfg = managed_config_for(resolved.plugin_slug, cluster, kind=service.kind, variant=service.variant or "")
    if credentials:
        from core.app_deploy import driver_for_capability

        if not hasattr(cfg, "secrets_backend"):
            raise ValueError("slice driver does not expose durable credentials")
        cfg = replace(cfg, secrets_backend=driver_for_capability(cluster, "secrets"))
    return resolved.driver_cls(config=cfg)


def validate_attachment(service, preview, driver, spec):
    """Refuse a previously recorded slice that cannot belong to this consumer."""
    from astrolift_services.models import ManagedServiceAttachment

    attachment = (
        ManagedServiceAttachment.objects.select_for_update()
        .filter(managed_service=service, app_environment=preview)
        .first()
    )
    if attachment is None or not attachment.slice_handle:
        return attachment
    if not callable(getattr(driver, "slice_binding", None)):
        raise ValueError("persisted slice driver cannot reconstruct its binding")
    binding = driver.slice_binding(spec)
    parent, child = unpack(service.backend_ref), unpack(attachment.slice_handle)
    database = binding.env_vars.get("POSTGRES_DB")
    if (
        child.kind != "postgres_slice"
        or child.cluster_id != parent.cluster_id
        or child.namespace != parent.namespace
        or database is None
        or child.name != database.literal
    ):
        raise ValueError("persisted preview slice does not match its current consumer and parent")
    return attachment


def binding_for_preview(service, preview_env):
    from astrolift_services.models import ManagedServiceAttachment

    attachment = ManagedServiceAttachment.objects.filter(
        managed_service=service, app_environment=preview_env
    ).first()
    if attachment is None:
        return None
    if not attachment.slice_handle:
        if service.registered_app_id and service.app_environment_id != preview_env.pk:
            raise ValueError(
                "inherited app service attachment has no durable slice identity; reconcile it before deployment"
            )
        return None
    current, preview, cluster, spec = live_slice_source(service, preview_env)
    driver = slice_driver_registration(current, cluster).driver_cls()
    if not getattr(driver, "supports_slicing", lambda: False)():
        raise ValueError("persisted slice driver cannot reconstruct its binding")
    validate_attachment(current, preview, driver, spec)
    return driver.slice_binding(spec)
