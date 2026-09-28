"use client";

import {
  InstanceAdminControlsView,
  InstanceDetailView,
  WorkflowInstancesPanelView,
} from "@/components/screens/workflows/list/WorkflowInstancesPanel";
import {
  useInstanceAdminControls,
  useWorkflowInstanceDetailPanel,
  useWorkflowInstancesPanel,
} from "@/components/screens/workflows/list/use-workflow-instances";
import type { WorkflowInstance } from "@/graphql/workflows/workflows.types";

/**
 * Temporal workflow instances (#437). The views own the markup; the detail
 * panel and the admin controls each get a container so their hooks run only
 * while they are rendered.
 */
export function WorkflowInstancesPanel(props: {
  workflowType?: string;
  initialStatus?: string;
  isAdmin?: boolean;
}) {
  const panel = useWorkflowInstancesPanel(props);
  return (
    <WorkflowInstancesPanelView
      {...panel}
      detail={
        <InstanceDetail
          workflowId={panel.selectedWorkflowId}
          isAdmin={panel.isAdmin}
          onClose={() => panel.setSelectedWorkflowId(null)}
          onAfterMutation={panel.refetch}
        />
      }
    />
  );
}

function InstanceDetail({
  workflowId,
  isAdmin,
  onClose,
  onAfterMutation,
}: {
  workflowId: string | null;
  isAdmin: boolean;
  onClose: () => void;
  onAfterMutation: () => void;
}) {
  const detail = useWorkflowInstanceDetailPanel(workflowId);
  return (
    <InstanceDetailView
      {...detail}
      isAdmin={isAdmin}
      onClose={onClose}
      adminControls={
        workflowId ? (
          <AdminControls
            workflowId={workflowId}
            onAfter={() => {
              detail.refetch();
              onAfterMutation();
            }}
          />
        ) : null
      }
    />
  );
}

function AdminControls({ workflowId, onAfter }: { workflowId: string; onAfter: () => void }) {
  return <InstanceAdminControlsView {...useInstanceAdminControls(workflowId, onAfter)} />;
}

// Re-export the inner instance type so the parent page can compose
// without a circular dependency on the graphql module.
export type { WorkflowInstance };
