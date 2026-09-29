"use client";

import { DeployModelScreen } from "@/components/screens/models/DeployModelScreen";
import { useDeployModel } from "@/components/screens/models/use-deploy-model";

export default function DeployModelPage() {
  return <DeployModelScreen {...useDeployModel()} />;
}
