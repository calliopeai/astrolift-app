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
    log.info("render_manifests placeholder", extra={"deployment_id": deployment_id})
    return {}


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
