"use client";

import { StartDeploymentPage } from "@/components/screens/deployments/StartDeploymentPage";
import { useStartDeployment } from "@/components/screens/deployments/use-start-deployment";

export function StartDeploymentClient() {
  return <StartDeploymentPage {...useStartDeployment()} />;
}
