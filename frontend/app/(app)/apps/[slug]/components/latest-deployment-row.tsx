"use client";

import { LatestDeploymentRow as LatestDeploymentRowView } from "@/components/screens/apps/detail/LatestDeploymentRow";
import { useLatestDeployment } from "@/components/screens/apps/detail/use-latest-deployment";

import { appPath, useAppChrome } from "./app-chrome-context";

/** The overview's latest-deployment row; its polled query runs here. */
export function LatestDeploymentRow({ appSlug }: { appSlug: string }) {
  const chrome = useAppChrome();
  return (
    <LatestDeploymentRowView {...useLatestDeployment(appSlug)} appHref={appPath(chrome, appSlug)} />
  );
}
