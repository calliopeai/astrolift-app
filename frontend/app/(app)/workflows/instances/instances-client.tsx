"use client";

import { WorkflowInstancesScreen } from "@/components/screens/workflows/list/WorkflowInstancesScreen";
import { useWorkflowsEntitlement } from "@/graphql/workflows/tiered.hooks";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { WorkflowInstancesPanel } from "../instances-panel";

/**
 * The platform instances page. The panel, and its query, mount only for a
 * viewer with `audit_log.read`, the grant the old Running tab checked;
 * cancel and terminate follow the workflows module's run grant, as there.
 */
export function InstancesClient() {
  const permissions = useMyPermissions();
  const { canRun } = useWorkflowsEntitlement();
  const canView = permissions.can("audit_log.read");
  return (
    <WorkflowInstancesScreen
      canView={canView}
      loading={permissions.loading && !canView}
      panel={canView ? <WorkflowInstancesPanel initialStatus="RUNNING" isAdmin={canRun} /> : null}
    />
  );
}
