"use client";

import { usePathname } from "next/navigation";

import { AppPlatformLinksView } from "@/components/screens/agents/detail/AppPlatformLinks";

/**
 * The agent shell's platform sub-page row, wired to the pathname. The view
 * lives in components/screens/agents/detail/AppPlatformLinks.
 */
export function AppPlatformLinks({ agentSlug }: { agentSlug: string }) {
  const pathname = usePathname() ?? "";
  return <AppPlatformLinksView agentSlug={agentSlug} pathname={pathname} />;
}
