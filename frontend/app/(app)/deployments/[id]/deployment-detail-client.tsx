"use client";

import { DeploymentDetailScreen } from "@/components/screens/deployments/DeploymentDetailScreen";
import { useDeploymentDetail } from "@/components/screens/deployments/use-deployment-detail";

export function DeploymentDetailClient({ id }: { id: string }) {
  return <DeploymentDetailScreen {...useDeploymentDetail(id)} />;
}
