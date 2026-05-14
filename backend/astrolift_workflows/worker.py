"""
Temporal worker registration helpers.

The worker process imports ``WORKFLOWS`` and ``ACTIVITIES`` and
passes them to ``temporalio.worker.Worker``. New workflows /
activities are added to those tuples; nothing else needs to change.
"""

from __future__ import annotations

from astrolift_workflows.activities import (
    apply_manifests,
    apply_platform_rbac,
    create_promotion_deployment,
    create_rollback_deployment,
    dispatch_cron_deploys,
    health_check,
    mark_app_provisioning,
    mark_app_ready,
    mark_deploying,
    mark_error,
    mark_managed,
    mark_managing,
    mark_running,
    poll_rollout,
    pre_flight,
    probe_capabilities,
    provision_managed_services_initial,
    provision_namespace,
    provision_registry_repo,
    render_manifests,
    run_preflight_job,
    update_secrets,
    verify_reachability,
    wait_dns,
)
from astrolift_workflows.workflows import (
    BringClusterIntoManagementWorkflow,
    CronDeployTickWorkflow,
    DeployAppWorkflow,
    OnboardAppWorkflow,
    PromoteDeploymentWorkflow,
    RollbackDeploymentWorkflow,
    TearDownPreviewWorkflow,
)

WORKFLOWS = (
    BringClusterIntoManagementWorkflow,
    CronDeployTickWorkflow,
    DeployAppWorkflow,
    OnboardAppWorkflow,
    PromoteDeploymentWorkflow,
    RollbackDeploymentWorkflow,
    TearDownPreviewWorkflow,
)

ACTIVITIES = (
    apply_manifests,
    apply_platform_rbac,
    create_promotion_deployment,
    create_rollback_deployment,
    dispatch_cron_deploys,
    health_check,
    mark_app_provisioning,
    mark_app_ready,
    mark_deploying,
    mark_error,
    mark_managed,
    mark_managing,
    mark_running,
    poll_rollout,
    pre_flight,
    probe_capabilities,
    provision_managed_services_initial,
    provision_namespace,
    provision_registry_repo,
    render_manifests,
    run_preflight_job,
    update_secrets,
    verify_reachability,
    wait_dns,
)


# Task queue names mirror specs/06 §3 — one per concern.
TASK_QUEUE_DEPLOY = "astrolift.deploy"
TASK_QUEUE_PROVISION = "astrolift.provision"
TASK_QUEUE_OBSERVABILITY = "astrolift.observability"
