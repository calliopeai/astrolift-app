"""Workflow definitions.

Each workflow is a class decorated with ``@workflow.defn``. Activities
are invoked with ``workflow.execute_activity`` so retries + timeouts
are configured at the workflow level rather than per-call.

These are skeletons: they orchestrate the spec'd step list and use
the no-op activity implementations until provider plugins ship. The
sequence is the contract — bodies fill in.
"""

from astrolift_workflows.workflows.bring_cluster_into_management import (
    BringClusterIntoManagementWorkflow,
)
from astrolift_workflows.workflows.cron_deploy_tick import CronDeployTickWorkflow
from astrolift_workflows.workflows.decommission_cluster import (
    DecommissionClusterWorkflow,
)
from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow
from astrolift_workflows.workflows.deprovision_managed_service import (
    DeprovisionManagedServiceWorkflow,
)
from astrolift_workflows.workflows.deregister_app import DeregisterAppWorkflow
from astrolift_workflows.workflows.install_cluster_prereqs import (
    InstallClusterPrereqsWorkflow,
)
from astrolift_workflows.workflows.migrate_app import MigrateAppWorkflow
from astrolift_workflows.workflows.onboard_app import OnboardAppWorkflow
from astrolift_workflows.workflows.promote_deployment import PromoteDeploymentWorkflow
from astrolift_workflows.workflows.rollback_deployment import RollbackDeploymentWorkflow
from astrolift_workflows.workflows.scheduled import (
    CapturePlatformCostSnapshotWorkflow,
    DriftDetectionWorkflow,
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

__all__ = [
    "BringClusterIntoManagementWorkflow",
    "CapturePlatformCostSnapshotWorkflow",
    "CronDeployTickWorkflow",
    "DecommissionClusterWorkflow",
    "DeleteSecretBundleFromClustersWorkflow",
    "DeployAppWorkflow",
    "DeprovisionManagedServiceWorkflow",
    "DeregisterAppWorkflow",
    "DriftDetectionWorkflow",
    "InstallClusterPrereqsWorkflow",
    "MigrateAppWorkflow",
    "OnboardAppWorkflow",
    "PollScheduledJobRunsWorkflow",
    "PreviewGarbageCollectWorkflow",
    "PromoteDeploymentWorkflow",
    "PruneAuditLogWorkflow",
    "PruneStaleSessionsWorkflow",
    "ReconcileClusterCapabilitiesWorkflow",
    "RehealWebhookSubscriptionsWorkflow",
    "RollbackDeploymentWorkflow",
    "RotateSecretBundleWorkflow",
    "SecretBundleScheduledRefreshWorkflow",
    "TearDownAppWorkflow",
    "TearDownPreviewWorkflow",
    "ValidateCustomDomainWorkflow",
]
