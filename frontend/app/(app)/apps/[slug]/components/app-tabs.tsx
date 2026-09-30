"use client";

import { usePathname } from "next/navigation";

import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import { useAppChrome } from "@/lib/app-chrome-context";

interface AppTabsProps {
  slug: string;
  /**
   * Fallback for a path the tab model does not know: a tab key or a former
   * route's name (`"console"`, `"members"`). Otherwise inferred from pathname.
   */
  active?: string;
}

/**
 * The app detail tab bar, wired to the ambient app chrome and pathname. The
 * view lives in components/screens/apps/detail/AppTabs.
 */
export function AppTabs({ slug, active }: AppTabsProps) {
  const { basePath, agentShell, framed } = useAppChrome();
  const pathname = usePathname() ?? "";

  // In the agent shell the AgentDetailShell already renders the agent's BROCS
  // pillar bar + the app-platform-links row; a second app-style tab bar would
  // duplicate chrome. Suppress it — this is the single mechanism that hides
  // AppTabs across all 13 shared clients without editing each one. Inside
  // the app frame the frame's ShellHeader already carries the one row.
  if (agentShell || framed) return null;

  return <AppTabsView slug={slug} basePath={basePath} pathname={pathname} active={active} />;
}
