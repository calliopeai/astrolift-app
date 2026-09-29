"""ECR deployment pins complement the infrastructure's age/count cleanup job."""

from __future__ import annotations


def _containers(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"containers", "initContainers"} and isinstance(child, list):
                for container in child:
                    if isinstance(container, dict) and isinstance(container.get("image"), str):
                        yield container
            else:
                yield from _containers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _containers(child)


def retain_deployment_images(deployment, resources):
    """Fail the rollout before apply if its ECR images cannot be protected."""
    from core.app_deploy import driver_for_capability

    cluster = deployment.app_environment.tenant_cluster
    if cluster.provider_plugin.slug != "aws":
        return
    containers = list(_containers(resources))
    pins = []
    if containers:
        driver = driver_for_capability(cluster, "registry")
        pins = driver.retain_deployment_images(
            [container["image"] for container in containers],
            environment=str(deployment.app_environment.guid),
            deployment=str(deployment.guid),
        )
    snapshot = dict(deployment.config_snapshot or {})
    # Preserve earlier pins on a retry whose rendered image set changed.
    prefix = f"retain-astrolift-{deployment.app_environment.guid.hex}-{deployment.guid.hex}-"
    existing = [pin for pin in snapshot.get("ecr_retention_pins", []) if pin["tag"].startswith(prefix)]
    by_tag = {(pin["repository"], pin["tag"]): pin for pin in [*existing, *pins]}
    if by_tag or "ecr_retention_pins" in snapshot:
        snapshot["ecr_retention_pins"] = list(by_tag.values())
        deployment.config_snapshot = snapshot
        deployment.save(update_fields=["config_snapshot", "updated_at", "version"])
    resolved = {pin["source_ref"]: pin["pinned_ref"] for pin in pins}
    for container in containers:
        container["image"] = resolved.get(container["image"], container["image"])


def release_obsolete_deployment_images(deployment):
    """Keep ten successful rollouts per environment/workload, plus live pins.

    Failed or in-flight rollouts may still have live pods, so their pins are
    never automatically retired here. Errors retain extra images, not fewer.
    """
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import driver_for_capability

    cluster = deployment.app_environment.tenant_cluster
    if cluster.provider_plugin.slug != "aws":
        return
    history = Deployment.all_objects.filter(
        registered_app_id=deployment.registered_app_id,
        app_environment_id=deployment.app_environment_id,
        workload_id=deployment.workload_id,
        status__in=[Deployment.Status.RUNNING, Deployment.Status.SUPERSEDED, Deployment.Status.ROLLED_BACK],
    ).order_by("-created_at", "-id")
    keep_ids = list(history.values_list("id", flat=True)[:10])
    obsolete = history.exclude(pk__in=keep_ids).exclude(status=Deployment.Status.RUNNING)
    driver = None
    for prior in obsolete.iterator():
        pins = (prior.config_snapshot or {}).get("ecr_retention_pins", [])
        if not pins:
            continue
        if driver is None:
            driver = driver_for_capability(cluster, "registry")
        driver.release_deployment_images(
            pins,
            environment=str(deployment.app_environment.guid),
            deployment=str(prior.guid),
        )
