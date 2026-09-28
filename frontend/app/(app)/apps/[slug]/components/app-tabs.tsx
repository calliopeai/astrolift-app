"use client";

import { usePathname } from "next/navigation";

import { type AppTabKey, AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import { useAppChrome } from "./app-chrome-context";

interface AppTabsProps {
  slug: string;
  /** Explicit override for the active section; otherwise inferred from pathname. */
  active?: AppTabKey;
}

/**
 * The app detail tab bar, wired to the ambient app chrome and pathname. The
 * view lives in components/screens/apps/detail/AppTabs.
 */
export function AppTabs({ slug, active }: AppTabsProps) {
  const { basePath, agentShell } = useAppChrome();
  const pathname = usePathname() ?? "";

  // In the agent shell the AgentDetailShell already renders the agent's BROCS
  // pillar bar + the app-platform-links row; a second app-style tab bar would
  // duplicate chrome. Suppress it — this is the single mechanism that hides
  // AppTabs across all 13 shared clients without editing each one.
  if (agentShell) return null;

  return <AppTabsView slug={slug} basePath={basePath} pathname={pathname} active={active} />;
}
