"""
Temporal worker registration helpers.

The worker process imports ``WORKFLOWS`` and ``ACTIVITIES`` and
passes them to ``temporalio.worker.Worker``. New workflows /
activities are added to those tuples; nothing else needs to change.
"""

from __future__ import annotations

from astrolift_workflows.activities import (
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
from astrolift_workflows.workflows import (
    DeployAppWorkflow,
    OnboardAppWorkflow,
    PromoteDeploymentWorkflow,
    RollbackDeploymentWorkflow,
    TearDownPreviewWorkflow,
)

WORKFLOWS = (
    DeployAppWorkflow,
    OnboardAppWorkflow,
    PromoteDeploymentWorkflow,
    RollbackDeploymentWorkflow,
    TearDownPreviewWorkflow,
)

ACTIVITIES = (
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


# Task queue names mirror specs/06 §3 — one per concern.
TASK_QUEUE_DEPLOY = "astrolift.deploy"
TASK_QUEUE_PROVISION = "astrolift.provision"
TASK_QUEUE_OBSERVABILITY = "astrolift.observability"
