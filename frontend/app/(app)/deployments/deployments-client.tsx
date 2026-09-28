"use client";

import { DeploymentsScreen } from "@/components/screens/deployments/DeploymentsScreen";
import { useDeployments } from "@/components/screens/deployments/use-deployments";

export function DeploymentsClient() {
  return <DeploymentsScreen {...useDeployments()} />;
}
