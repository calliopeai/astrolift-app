"use client";

import { useWorkflowInstancesList } from "@/components/screens/workflows/list/use-workflow-instances";
import { WorkflowInstancesScreen } from "@/components/screens/workflows/list/WorkflowInstancesScreen";
import { useWorkflowsEntitlement } from "@/graphql/workflows/tiered.hooks";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { InstanceDetail } from "../instances-panel";

/**
 * The platform instances page. The list, and its query, mount only for a
 * viewer with `audit_log.read`, the grant the old Running tab checked;
 * cancel and terminate follow the workflows module's run grant, as there.
 */
export function InstancesClient() {
  const permissions = useMyPermissions();
  const { canRun } = useWorkflowsEntitlement();
  const canView = permissions.can("audit_log.read");
  if (!canView)
    return <WorkflowInstancesScreen access={permissions.loading ? "loading" : "denied"} />;
  return <InstancesList isAdmin={canRun} />;
}

function InstancesList({ isAdmin }: { isAdmin: boolean }) {
  const state = useWorkflowInstancesList();
  return (
    <WorkflowInstancesScreen
      access="granted"
      {...state}
      detail={
        state.selectedWorkflowId ? (
          <InstanceDetail
            workflowId={state.selectedWorkflowId}
            isAdmin={isAdmin}
            onClose={state.onCloseInstance}
            onAfterMutation={state.onRetry}
          />
        ) : null
      }
    />
  );
}
