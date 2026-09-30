"use client";

import type * as React from "react";

import { useDeploymentPanel } from "@/components/screens/apps/controls/use-deployment-panel";
import { LatestDeployPanel as LatestDeployPanelView } from "@/components/screens/apps/overview/LatestDeployPanel";
import { ManagedServicesPanel as ManagedServicesPanelView } from "@/components/screens/apps/overview/ManagedServicesPanel";
import { OwnershipPanel as OwnershipPanelView } from "@/components/screens/apps/overview/OwnershipPanel";
import { useManagedServicesSummary } from "@/components/screens/apps/overview/use-managed-services-summary";
import { useOwnership } from "@/components/screens/apps/overview/use-ownership";

import { appPath, useAppChrome } from "@/lib/app-chrome-context";

/**
 * Containers for the Overview panels that have a query of their own. Each
 * runs its hook and hands the view its chrome-aware links.
 */

export function LatestDeployPanel({
  appSlug,
  pending,
}: {
  appSlug: string;
  pending?: React.ReactNode;
}) {
  const chrome = useAppChrome();
  return (
    <LatestDeployPanelView
      {...useDeploymentPanel(appSlug)}
      appHref={appPath(chrome, appSlug)}
      pending={pending}
    />
  );
}

export function ManagedServicesPanel({ appSlug }: { appSlug: string }) {
  const chrome = useAppChrome();
  return (
    <ManagedServicesPanelView
      {...useManagedServicesSummary(appSlug)}
      managedServicesHref={`${appPath(chrome, appSlug, "workloads")}?section=managed-services`}
    />
  );
}

export function OwnershipPanel({
  appSlug,
  projectName,
  teamName,
}: {
  appSlug: string;
  projectName: string;
  teamName: string;
}) {
  const chrome = useAppChrome();
  return (
    <OwnershipPanelView
      {...useOwnership(appSlug)}
      projectName={projectName}
      teamName={teamName}
      settingsHref={appPath(chrome, appSlug, "settings")}
    />
  );
}
