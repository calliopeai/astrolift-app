"""
Activities for the app onboarding + deploy workflows.

These wrap the platform models behind ``@activity.defn`` so the
workflow definition stays free of Django imports (Temporal runs
workflows in a sandbox that disallows non-deterministic imports).

The bodies are deliberately thin: each activity does exactly one
durable thing. Provider-specific logic happens behind the
``astrolift_drivers`` interfaces, which we resolve at activity entry.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities")


@activity.defn(name="astrolift.app.mark_provisioning")
async def mark_app_provisioning(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    if app.provisioning_status == RegisteredApp.ProvisioningStatus.PROVISIONING.value:
        return
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.PROVISIONING)


@activity.defn(name="astrolift.app.mark_ready")
async def mark_app_ready(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.READY)


@activity.defn(name="astrolift.app.provision_namespace")
async def provision_namespace(registered_app_id: int) -> None:
    activity.heartbeat()
    log.info("provision_namespace placeholder", extra={"registered_app_id": registered_app_id})


@activity.defn(name="astrolift.app.provision_registry_repo")
async def provision_registry_repo(registered_app_id: int) -> str:
    activity.heartbeat()
    log.info("provision_registry_repo placeholder", extra={"registered_app_id": registered_app_id})
    return ""


@activity.defn(name="astrolift.app.provision_managed_services_initial")
async def provision_managed_services_initial(
    registered_app_id: int,
    app_environment_id: int,
) -> list[int]:
    log.info(
        "provision_managed_services_initial placeholder",
        extra={"registered_app_id": registered_app_id, "app_environment_id": app_environment_id},
    )
    return []


@activity.defn(name="astrolift.deploy.pre_flight")
async def pre_flight(deployment_id: int) -> None:
    activity.heartbeat()
    log.info("pre_flight placeholder", extra={"deployment_id": deployment_id})


@activity.defn(name="astrolift.deploy.mark_deploying")
async def mark_deploying(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.get(pk=deployment_id)
    d.transition_to(Deployment.Status.DEPLOYING)


@activity.defn(name="astrolift.deploy.render_manifests")
async def render_manifests(deployment_id: int) -> dict[str, Any]:
    """Build the list of Kubernetes resource dicts for ``apply_manifests``.

    Reads the Deployment row, normalizes the registered-app's stored
    manifest, and runs the pure renderer in
    ``astrolift_manifest.render``. The rendered output is returned as
    ``{"resources": [...]}`` so the workflow has a single-key Temporal
    payload (stable across renderer evolution) and so ``apply_manifests``
    can pick up the same shape unchanged.
    """
    from asgiref.sync import sync_to_async

    from astrolift_lifecycle.models import Deployment
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_manifest.render import render_manifests as _render

    activity.heartbeat()

    def _gather():
        d = (
            Deployment.all_objects.select_related(
                "registered_app", "app_environment"
            )
            .get(pk=deployment_id)
        )
        app = d.registered_app
        env = d.app_environment
        # Render off the stored TOML — never re-fetch from the repo at
        # apply time so deployments are reproducible after force-pushes.
        manifest = normalize(parse_raw(app.manifest_raw), defaults=NormalizationDefaults())
        return manifest, app, env, d

    manifest, app, env, d = await sync_to_async(_gather)()

    namespace = app.k8s_namespace or f"{app.organization.slug}-{app.slug}"
    resources = _render(
        manifest,
        namespace=namespace,
        image_tag=d.image_tag or "latest",
        image_repository=app.registry_repo_uri or app.slug,
        environment_name=env.name,
    )
    log.info(
        "render_manifests produced %d resource(s)",
        len(resources),
        extra={"deployment_id": deployment_id},
    )
    return {"resources": resources}


@activity.defn(name="astrolift.deploy.apply_manifests")
async def apply_manifests(deployment_id: int) -> None:
    activity.heartbeat()
    log.info("apply_manifests placeholder", extra={"deployment_id": deployment_id})


@activity.defn(name="astrolift.deploy.update_secrets")
async def update_secrets(deployment_id: int) -> None:
    log.info("update_secrets placeholder", extra={"deployment_id": deployment_id})


@activity.defn(name="astrolift.deploy.wait_dns")
async def wait_dns(deployment_id: int) -> None:
    activity.heartbeat()


@activity.defn(name="astrolift.deploy.poll_rollout")
async def poll_rollout(deployment_id: int) -> bool:
    activity.heartbeat()
    return True


@activity.defn(name="astrolift.deploy.health_check")
async def health_check(deployment_id: int) -> bool:
    return True


@activity.defn(name="astrolift.deploy.mark_running")
async def mark_running(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.get(pk=deployment_id)
    d.transition_to(Deployment.Status.RUNNING)
