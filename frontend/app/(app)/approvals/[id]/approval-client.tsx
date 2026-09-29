"use client";

import { ApprovalHistoryPanel } from "@/components/screens/approvals/ApprovalHistory";
import { ApprovalScreen } from "@/components/screens/approvals/ApprovalScreen";
import { useApproval, useApprovalHistory } from "@/components/screens/approvals/use-approval";

/**
 * Approval page. The screen owns the markup; the history panel gets a
 * container so its query runs only once the deployment has loaded.
 */
export function ApprovalClient({ id }: { id: string }) {
  const approval = useApproval(id);
  const deploymentId = approval.deployment?.id;
  return (
    <ApprovalScreen
      {...approval}
      history={deploymentId ? <ApprovalHistory deploymentId={deploymentId} /> : undefined}
    />
  );
}

function ApprovalHistory({ deploymentId }: { deploymentId: string }) {
  return <ApprovalHistoryPanel {...useApprovalHistory(deploymentId)} />;
}
