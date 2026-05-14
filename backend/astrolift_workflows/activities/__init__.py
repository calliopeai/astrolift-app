"""Temporal activities — durable, idempotent units of work.

Each activity is a thin wrapper around a platform model + driver
call. Implementations are deliberately no-ops at this stage; the
worker will replace them with real driver-backed implementations as
provider plugins land in the ``astrolift-providers`` repo.
"""

from astrolift_workflows.activities.app_lifecycle import (
    apply_manifests,
    create_promotion_deployment,
    create_rollback_deployment,
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
from astrolift_workflows.activities.cluster_management import (
    apply_platform_rbac,
    mark_error,
    mark_managed,
    mark_managing,
    probe_capabilities,
    run_preflight_job,
    verify_reachability,
)
from astrolift_workflows.activities.cron_deploy import dispatch_cron_deploys

__all__ = [
    "apply_manifests",
    "apply_platform_rbac",
    "create_promotion_deployment",
    "create_rollback_deployment",
    "dispatch_cron_deploys",
    "health_check",
    "mark_app_provisioning",
    "mark_app_ready",
    "mark_deploying",
    "mark_error",
    "mark_managed",
    "mark_managing",
    "mark_running",
    "poll_rollout",
    "pre_flight",
    "probe_capabilities",
    "provision_managed_services_initial",
    "provision_namespace",
    "provision_registry_repo",
    "render_manifests",
    "run_preflight_job",
    "update_secrets",
    "verify_reachability",
    "wait_dns",
]
