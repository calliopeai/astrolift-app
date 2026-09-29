"use client";

import { PendingDeploymentsView } from "@/components/screens/apps/controls/PendingDeployments";
import { usePendingDeployments } from "@/components/screens/apps/controls/use-pending-deployments";

/** Approval queue: the hook owns the polled query, the view the markup. */
export function PendingDeployments({ appSlug }: { appSlug: string }) {
  return <PendingDeploymentsView {...usePendingDeployments(appSlug)} />;
}
