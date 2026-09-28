"use client";

import { DeploymentPanelView } from "@/components/screens/apps/controls/DeploymentPanel";
import { useDeploymentPanel } from "@/components/screens/apps/controls/use-deployment-panel";

/** Current-deployment panel: the hook owns the live query, the view the markup. */
export function DeploymentPanel({ appSlug }: { appSlug: string }) {
  return <DeploymentPanelView {...useDeploymentPanel(appSlug)} />;
}
