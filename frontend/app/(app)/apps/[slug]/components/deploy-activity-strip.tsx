"use client";

import { DeployActivityStrip as DeployActivityStripView } from "@/components/screens/apps/detail/DeployActivityStrip";
import { useDeployActivity } from "@/components/screens/apps/detail/use-deploy-activity";

import { appPath, useAppChrome } from "./app-chrome-context";

interface Props {
  appSlug: string;
  limit?: number;
}

/** The overview's deploy heatmap; its polled deployments query runs here. */
export function DeployActivityStrip({ appSlug, limit = 20 }: Props) {
  const chrome = useAppChrome();
  return (
    <DeployActivityStripView
      {...useDeployActivity(appSlug, limit)}
      appHref={appPath(chrome, appSlug)}
    />
  );
}
