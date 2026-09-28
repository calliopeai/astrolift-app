"use client";

import { DeploymentsScreen } from "@/components/screens/deployments/DeploymentsScreen";
import { useDeployments } from "@/components/screens/deployments/use-deployments";

import { StartDeploymentDialog } from "./start-deployment-dialog";

export function DeploymentsClient() {
  return (
    <DeploymentsScreen
      {...useDeployments()}
      renderStartDialog={(props) => <StartDeploymentDialog {...props} />}
    />
  );
}
