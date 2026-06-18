"""Workflow definitions.

Each workflow is a class decorated with ``@workflow.defn``. Activities
are invoked with ``workflow.execute_activity`` so retries + timeouts
are configured at the workflow level rather than per-call.

These are skeletons: they orchestrate the spec'd step list and use
the no-op activity implementations until provider plugins ship. The
sequence is the contract — bodies fill in.
"""

from astrolift_workflows.workflows.agent_cron_tick import AgentCronTickWorkflow
from astrolift_workflows.workflows.agent_loop_tick import AgentLoopTickWorkflow
from astrolift_workflows.workflows.agent_scale_tick import AgentScaleTickWorkflow
from astrolift_workflows.workflows.bring_cluster_into_management import (
    BringClusterIntoManagementWorkflow,
)
from astrolift_workflows.workflows.build_preview import BuildPreviewWorkflow
from astrolift_workflows.workflows.cron_deploy_tick import CronDeployTickWorkflow
from astrolift_workflows.workflows.decommission_cluster import (
    DecommissionClusterWorkflow,
)
from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow
from astrolift_workflows.workflows.deprovision_managed_service import (
    DeprovisionManagedServiceWorkflow,
)
from astrolift_workflows.workflows.deregister_app import DeregisterAppWorkflow
from astrolift_workflows.workflows.dev_environment import (
    CreateDevEnvironmentWorkflow,
    SyncDevEnvironmentFilesWorkflow,
)
from astrolift_workflows.workflows.dispatch_agent_task import DispatchAgentTaskWorkflow
from astrolift_workflows.workflows.install_cluster_prereqs import (
    InstallClusterPrereqsWorkflow,
)
from astrolift_workflows.workflows.migrate_app import MigrateAppWorkflow
from astrolift_workflows.workflows.onboard_app import OnboardAppWorkflow
from astrolift_workflows.workflows.promote_deployment import PromoteDeploymentWorkflow
from astrolift_workflows.workflows.provision_managed_domain import (
    ProvisionManagedDomainWorkflow,
)
from astrolift_workflows.workflows.provision_namespace import NamespaceProvisionWorkflow
from astrolift_workflows.workflows.provision_registry import RegistryProvisionWorkflow
from astrolift_workflows.workflows.rollback_deployment import RollbackDeploymentWorkflow
from astrolift_workflows.workflows.scheduled import (
    CapturePlatformCostSnapshotWorkflow,
    DriftDetectionWorkflow,
    ExpirePendingApprovalDeploymentsWorkflow,
    PollScheduledJobRunsWorkflow,
    PreviewGarbageCollectWorkflow,
    PruneAuditLogWorkflow,
    PruneStaleSessionsWorkflow,
    ReconcileClusterCapabilitiesWorkflow,
    RehealWebhookSubscriptionsWorkflow,
)
from astrolift_workflows.workflows.secret_rotation import (
    DeleteSecretBundleFromClustersWorkflow,
    RotateSecretBundleWorkflow,
    SecretBundleScheduledRefreshWorkflow,
)
from astrolift_workflows.workflows.tear_down_app import TearDownAppWorkflow
from astrolift_workflows.workflows.tear_down_preview import TearDownPreviewWorkflow
from astrolift_workflows.workflows.validate_custom_domain import (
    ValidateCustomDomainWorkflow,
)
from astrolift_workflows.workflows.workflow_definition_run import (
    WorkflowDefinitionRunWorkflow,
)

__all__ = [
    "AgentCronTickWorkflow",
    "AgentLoopTickWorkflow",
    "AgentScaleTickWorkflow",
    "BringClusterIntoManagementWorkflow",
    "BuildPreviewWorkflow",
    "CapturePlatformCostSnapshotWorkflow",
    "CreateDevEnvironmentWorkflow",
    "CronDeployTickWorkflow",
    "DecommissionClusterWorkflow",
    "DeleteSecretBundleFromClustersWorkflow",
    "DeployAppWorkflow",
    "DeprovisionManagedServiceWorkflow",
    "DeregisterAppWorkflow",
    "DispatchAgentTaskWorkflow",
    "DriftDetectionWorkflow",
    "ExpirePendingApprovalDeploymentsWorkflow",
    "InstallClusterPrereqsWorkflow",
    "MigrateAppWorkflow",
    "NamespaceProvisionWorkflow",
    "OnboardAppWorkflow",
    "PollScheduledJobRunsWorkflow",
    "PreviewGarbageCollectWorkflow",
    "PromoteDeploymentWorkflow",
    "ProvisionManagedDomainWorkflow",
    "PruneAuditLogWorkflow",
    "PruneStaleSessionsWorkflow",
    "ReconcileClusterCapabilitiesWorkflow",
    "RegistryProvisionWorkflow",
    "RehealWebhookSubscriptionsWorkflow",
    "RollbackDeploymentWorkflow",
    "RotateSecretBundleWorkflow",
    "SecretBundleScheduledRefreshWorkflow",
    "SyncDevEnvironmentFilesWorkflow",
    "TearDownAppWorkflow",
    "TearDownPreviewWorkflow",
    "ValidateCustomDomainWorkflow",
    "WorkflowDefinitionRunWorkflow",
]
