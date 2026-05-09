"""Workflow definitions.

Each workflow is a class decorated with ``@workflow.defn``. Activities
are invoked with ``workflow.execute_activity`` so retries + timeouts
are configured at the workflow level rather than per-call.

These are skeletons: they orchestrate the spec'd step list and use
the no-op activity implementations until provider plugins ship. The
sequence is the contract — bodies fill in.
"""

from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow
from astrolift_workflows.workflows.onboard_app import OnboardAppWorkflow
from astrolift_workflows.workflows.promote_deployment import PromoteDeploymentWorkflow
from astrolift_workflows.workflows.rollback_deployment import RollbackDeploymentWorkflow
from astrolift_workflows.workflows.tear_down_preview import TearDownPreviewWorkflow

__all__ = [
    "DeployAppWorkflow",
    "OnboardAppWorkflow",
    "PromoteDeploymentWorkflow",
    "RollbackDeploymentWorkflow",
    "TearDownPreviewWorkflow",
]
