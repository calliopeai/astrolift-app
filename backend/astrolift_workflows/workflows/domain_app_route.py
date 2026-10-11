"""Original reviewed zone preparation/route operation, with bounded read polling."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.domain_app_route_inputs import ManagedDomainAppRouteInput

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.domain_app_route import reconcile_domain_app_route


@workflow.defn(name="ManagedDomainAppRouteWorkflow")
class ManagedDomainAppRouteWorkflow:
    @workflow.run
    async def run(self, input: ManagedDomainAppRouteInput) -> dict[str, str]:
        result = {
            "operation_id": input.operation_id,
            "status": "pending",
            "reason": "ROUTE_OBSERVATION_PENDING",
        }
        for _ in range(120):
            result = await workflow.execute_activity(
                reconcile_domain_app_route,
                input.operation_id,
                start_to_close_timeout=timedelta(seconds=120),
                heartbeat_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            if result["status"] != "pending":
                return result
            await workflow.sleep(timedelta(seconds=30))
        return result
