"use client";

import type * as React from "react";

import { EnvironmentsScreen } from "@/components/screens/environments/EnvironmentsScreen";
import { useEnvironments } from "@/components/screens/environments/use-environments";

/**
 * Apps › Environments, and the list embedded on an app's Settings tab
 * (and an agent's environments page, with its tabs) when appSlug is set.
 */
export function EnvironmentsClient({
  appSlug,
  tabs,
}: { appSlug?: string; tabs?: React.ReactNode } = {}) {
  return <EnvironmentsScreen {...useEnvironments(appSlug)} tabs={tabs} />;
}
