"use client";

import type * as React from "react";

import { EnvironmentsScreen } from "@/components/screens/environments/EnvironmentsScreen";
import { useEnvironments } from "@/components/screens/environments/use-environments";

/**
 * Global /environments list, and the per-app (and per-agent) environments
 * tab when appSlug and tabs are passed.
 */
export function EnvironmentsClient({
  appSlug,
  tabs,
}: { appSlug?: string; tabs?: React.ReactNode } = {}) {
  return <EnvironmentsScreen {...useEnvironments(appSlug)} appSlug={appSlug} tabs={tabs} />;
}
