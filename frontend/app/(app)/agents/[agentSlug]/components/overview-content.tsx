"use client";

import { AgentOverviewView } from "@/components/screens/agents/detail/AgentOverview";
import { useAgentOverview } from "@/components/screens/agents/detail/use-agent-overview";

import { useFramedAgent } from "./framed-agent";

/**
 * The agent's Overview tab, wired to its data. The view lives in
 * components/screens/agents/detail/AgentOverview.
 */
export function OverviewContent() {
  return <AgentOverviewView {...useAgentOverview(useFramedAgent())} />;
}
