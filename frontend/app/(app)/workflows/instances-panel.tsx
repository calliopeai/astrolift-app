"use client";

import {
  InstanceAdminControlsView,
  InstanceDetailView,
} from "@/components/screens/workflows/list/WorkflowInstancesPanel";
import {
  useInstanceAdminControls,
  useWorkflowInstanceDetailPanel,
} from "@/components/screens/workflows/list/use-workflow-instances";

/**
 * One Temporal instance (#437), opened from the Platform instances list.
 * The views own the markup; the detail and the admin controls each get a
 * container so their hooks run only while they are rendered.
 */
export function InstanceDetail({
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
