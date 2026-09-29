"use client";

import { AgentsListScreen } from "@/components/screens/agents/list/AgentsListScreen";
import { useAgentsList } from "@/components/screens/agents/list/use-agents-list";

/** The agents list. The screen owns the markup; the hook owns the data. */
export function AgentsClient() {
  return <AgentsListScreen {...useAgentsList()} />;
}
