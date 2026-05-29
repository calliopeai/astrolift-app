"""RegistryProvisionWorkflow — provision the app's container registry repo.

Child workflow of OnboardAppWorkflow. Wrapping the provision_registry_repo
activity in its own workflow gives it an independent Temporal history entry:
auditable, individually retriable, and targetable by a future
RebuildComponentWorkflow without re-running the full onboard.

Idempotent — provision_registry_repo fast-paths when
RegisteredApp.registry_repo_uri is already set.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import OnboardAppInput

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import provision_registry_repo

_ACTIVITY_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="RegistryProvisionWorkflow")
class RegistryProvisionWorkflow:
    """Provision the app's container registry repo + CI push role.

    Returns the registry URI string on success.
    """

    @workflow.run
    async def run(self, input: OnboardAppInput) -> str:
        uri: str = await workflow.execute_activity(
            provision_registry_repo,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        return uri
