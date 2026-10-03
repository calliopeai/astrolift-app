"use client";

import { useWorkflowInstancesList } from "@/components/screens/workflows/list/use-workflow-instances";
import { WorkflowInstancesScreen } from "@/components/screens/workflows/list/WorkflowInstancesScreen";
import { PlatformActivityScreen } from "@/components/screens/platform-activity/PlatformActivityScreen";
import { useWorkflowsEntitlement } from "@/graphql/workflows/tiered.hooks";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { InstanceDetail } from "../instances-panel";

/**
 * The platform instances page. The list, and its query, mount only for a
 * viewer with `audit_log.read`, the grant the old Running tab checked;
 * cancel and terminate follow the workflows module's run grant, as there.
 */
export function InstancesClient({ platformActivity = false }: { platformActivity?: boolean }) {
  const Screen = platformActivity ? PlatformActivityScreen : WorkflowInstancesScreen;
  const permissions = useMyPermissions();
  const { canRun } = useWorkflowsEntitlement();
  const canView = permissions.can("audit_log.read");
  if (!canView) return <Screen access={permissions.loading ? "loading" : "denied"} />;
  return <InstancesList isAdmin={canRun} platformActivity={platformActivity} />;
}

function InstancesList({
  isAdmin,
  platformActivity,
}: {
  isAdmin: boolean;
  platformActivity: boolean;
}) {
  const Screen = platformActivity ? PlatformActivityScreen : WorkflowInstancesScreen;
  const state = useWorkflowInstancesList();
  return (
    <Screen
      access="granted"
      {...state}
      detail={
        state.selectedWorkflowId ? (
          <InstanceDetail
            key={`${state.selectedWorkflowId}:${state.selectedRunId}`}
            workflowId={state.selectedWorkflowId}
            runId={state.selectedRunId}
            isAdmin={isAdmin}
            onClose={state.onCloseInstance}
            onAfterMutation={state.onRetry}
          />
        ) : null
      }
    />
  );
}
