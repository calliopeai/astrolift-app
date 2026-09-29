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


def retain_deployment_image_refs(deployment, refs: list[str]) -> dict[str, str]:
    """Protect image refs before any service or cluster write; persist owned pins."""
    import re

    from django.db import transaction

    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import driver_for_capability

    cluster = deployment.app_environment.tenant_cluster
    if cluster.provider_plugin.slug != "aws":
        return {}
    # Different rollout stages may hold stale Deployment instances. Lock the
    # current snapshot so one rollout keeps the first protected source digest.
    with transaction.atomic():
        current = Deployment.all_objects.select_for_update().get(pk=deployment.pk)
        snapshot = dict(current.config_snapshot or {})
        prefix = f"retain-astrolift-{deployment.app_environment.guid.hex}-{deployment.guid.hex}-"
        owned = [
            pin for pin in snapshot.get("ecr_retention_pins", []) if pin.get("tag", "").startswith(prefix)
        ]
        driver = driver_for_capability(cluster, "registry") if refs or owned else None
        registry_uri = driver._registry_uri() if owned else ""
        existing = []
        for pin in owned:
            digest = pin.get("digest", "")
            repository = pin.get("repository", "")
            source = pin.get("source_ref", "")
            canonical = f"{registry_uri}/{repository}@{digest}"
            source_repo = source.split("@", 1)[0].split(":", 1)[0]
            if (
                re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                and pin["tag"] == prefix + digest[7:]
                and source_repo == f"{registry_uri}/{repository}"
                and pin.get("pinned_ref") == canonical
            ):
                existing.append({**pin, "pinned_ref": canonical})
        resolved = {pin["source_ref"]: pin["pinned_ref"] for pin in existing}
        resolved.update({pin["pinned_ref"]: pin["pinned_ref"] for pin in existing})
        pending = [ref for ref in refs if ref not in resolved]
        pins = []
        if pending:
            pins = driver.retain_deployment_images(
                pending,
                environment=str(deployment.app_environment.guid),
                deployment=str(deployment.guid),
            )
        by_tag = {(pin["repository"], pin["tag"]): pin for pin in [*existing, *pins]}
        if by_tag or "ecr_retention_pins" in snapshot:
            snapshot["ecr_retention_pins"] = list(by_tag.values())
            current.config_snapshot = snapshot
            current.save(update_fields=["config_snapshot", "updated_at", "version"])
        deployment.config_snapshot = current.config_snapshot
        deployment.version = current.version
        resolved.update({pin["source_ref"]: pin["pinned_ref"] for pin in pins})
    return {ref: resolved[ref] for ref in refs if ref in resolved}


def retain_deployment_images(deployment, resources):
    """Fail the rollout before apply if its ECR images cannot be protected."""
    containers = list(_containers(resources))
    resolved = retain_deployment_image_refs(deployment, [container["image"] for container in containers])
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
