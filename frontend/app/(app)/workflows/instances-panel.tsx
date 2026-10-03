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
  runId,
  isAdmin,
  onClose,
  onAfterMutation,
}: {
  workflowId: string | null;
  runId: string | null;
  isAdmin: boolean;
  onClose: () => void;
  onAfterMutation: () => void;
}) {
  const detail = useWorkflowInstanceDetailPanel(workflowId, runId);
  return (
    <InstanceDetailView
      {...detail}
      isAdmin={isAdmin}
      onClose={onClose}
      onRefresh={runId ? detail.refetch : undefined}
      adminControls={
        workflowId && runId ? (
          <AdminControls
            workflowId={workflowId}
            runId={runId}
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

function AdminControls({
  workflowId,
  runId,
  onAfter,
}: {
  workflowId: string;
  runId: string;
  onAfter: () => void;
}) {
  return <InstanceAdminControlsView {...useInstanceAdminControls(workflowId, runId, onAfter)} />;
}
