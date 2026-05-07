"""Temporal activities — durable, idempotent units of work.

Each activity is a thin wrapper around a platform model + driver
call. Implementations are deliberately no-ops at this stage; the
worker will replace them with real driver-backed implementations as
provider plugins land in the ``astrolift-providers`` repo.
"""

from astrolift_workflows.activities.app_lifecycle import (
    apply_manifests,
    health_check,
    mark_app_provisioning,
    mark_app_ready,
    mark_deploying,
    mark_running,
    poll_rollout,
    pre_flight,
    provision_managed_services_initial,
    provision_namespace,
    provision_registry_repo,
    render_manifests,
    update_secrets,
    wait_dns,
)

__all__ = [
    "apply_manifests",
    "health_check",
    "mark_app_provisioning",
    "mark_app_ready",
    "mark_deploying",
    "mark_running",
    "poll_rollout",
    "pre_flight",
    "provision_managed_services_initial",
    "provision_namespace",
    "provision_registry_repo",
    "render_manifests",
    "update_secrets",
    "wait_dns",
]
