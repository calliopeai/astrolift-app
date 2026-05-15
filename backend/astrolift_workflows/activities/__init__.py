"""Temporal activities — durable, idempotent units of work.

Each activity is a thin wrapper around a platform model + driver
call. Activities resolve the relevant provider plugin (cluster,
secrets, dns, registry, managed-service) on entry and dispatch
through the canonical ``astrolift_drivers.registry`` interface so
workflow code stays free of Django + driver imports.
"""

from astrolift_workflows.activities.app_lifecycle import (
    apply_manifests,
    create_promotion_deployment,
    create_rollback_deployment,
    delete_preview_namespace,
    health_check,
    mark_app_provisioning,
    mark_app_ready,
    mark_deploying,
    mark_preview_torn_down,
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
from astrolift_workflows.activities.app_teardown import (
    delete_app_namespaces,
    list_app_managed_service_ids,
    mark_app_deregistered,
    mark_app_tearing_down,
    revoke_app_deploy_tokens,
    soft_delete_app_records,
)
from astrolift_workflows.activities.cluster_management import (
    apply_platform_rbac,
    ensure_cluster_drained,
    mark_decommissioned,
    mark_decommissioning,
    mark_error,
    mark_managed,
    mark_managing,
    probe_capabilities,
    remove_platform_rbac,
    run_preflight_job,
    teardown_cluster_infra,
    verify_reachability,
)
from astrolift_workflows.activities.cron_deploy import dispatch_cron_deploys
from astrolift_workflows.activities.install_prereqs import (
    install_cluster_prereqs,
)
from astrolift_workflows.activities.managed_service_lifecycle import (
    deprovision_managed_service,
    finalize_managed_service_deletion,
    mark_managed_service_deprovisioning,
)
from astrolift_workflows.activities.migration import (
    apply_to_target_cluster,
    drain_source_cluster,
    poll_rollout_on_target,
    switch_app_env_binding,
    validate_migration_target,
)
from astrolift_workflows.activities.scheduled import (
    capture_platform_cost_snapshot,
    detect_drift,
    gc_stale_previews,
    poll_scheduled_job_runs,
    prune_audit_log,
    reconcile_cluster_capabilities,
    reheal_webhook_subscriptions,
)

__all__ = [
    "apply_manifests",
    "apply_platform_rbac",
    "apply_to_target_cluster",
    "capture_platform_cost_snapshot",
    "create_promotion_deployment",
    "create_rollback_deployment",
    "delete_app_namespaces",
    "delete_preview_namespace",
    "deprovision_managed_service",
    "detect_drift",
    "dispatch_cron_deploys",
    "drain_source_cluster",
    "ensure_cluster_drained",
    "finalize_managed_service_deletion",
    "gc_stale_previews",
    "health_check",
    "install_cluster_prereqs",
    "list_app_managed_service_ids",
    "mark_app_deregistered",
    "mark_app_provisioning",
    "mark_app_ready",
    "mark_app_tearing_down",
    "mark_decommissioned",
    "mark_decommissioning",
    "mark_deploying",
    "mark_error",
    "mark_managed",
    "mark_managed_service_deprovisioning",
    "mark_managing",
    "mark_preview_torn_down",
    "mark_running",
    "poll_rollout",
    "poll_rollout_on_target",
    "poll_scheduled_job_runs",
    "pre_flight",
    "probe_capabilities",
    "provision_managed_services_initial",
    "provision_namespace",
    "provision_registry_repo",
    "prune_audit_log",
    "reconcile_cluster_capabilities",
    "reheal_webhook_subscriptions",
    "remove_platform_rbac",
    "render_manifests",
    "revoke_app_deploy_tokens",
    "run_preflight_job",
    "soft_delete_app_records",
    "switch_app_env_binding",
    "teardown_cluster_infra",
    "update_secrets",
    "validate_migration_target",
    "verify_reachability",
    "wait_dns",
]
