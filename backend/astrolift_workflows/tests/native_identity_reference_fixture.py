"""Actual sandbox fixture with no Django or activity imports."""

from temporalio import workflow

from astrolift_workflows.native_identity_inputs import AcceptedAppIdentityAuthority


@workflow.defn
class NativeIdentityReferenceWorkflow:
    @workflow.run
    async def run(self, reference: AcceptedAppIdentityAuthority) -> AcceptedAppIdentityAuthority:
        return reference
