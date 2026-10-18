"use client";

import { AgentInboxScreen } from "@/components/screens/inbox/AgentInboxScreen";
import { useAgentInbox } from "@/components/screens/inbox/use-agent-inbox";

/** The agent inbox (#2434). The screen owns the markup. */
export function AgentInboxClient() {
  return <AgentInboxScreen {...useAgentInbox()} />;
}
