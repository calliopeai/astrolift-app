"use client";

import { usePathname } from "next/navigation";

import { AgentTabsView } from "@/components/screens/agents/detail/AgentTabs";

interface AgentTabsProps {
  agentSlug: string;
}

/**
 * The per-agent BROCS pillar bar, wired to the pathname. The view lives in
 * components/screens/agents/detail/AgentTabs.
 */
export function AgentTabs({ agentSlug }: AgentTabsProps) {
  const pathname = usePathname() ?? "";
  return <AgentTabsView agentSlug={agentSlug} pathname={pathname} />;
}
