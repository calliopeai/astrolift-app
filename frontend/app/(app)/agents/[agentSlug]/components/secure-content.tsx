"use client";

import { AgentSecureScreen } from "@/components/screens/agents/detail/AgentSecure";

import { useFramedAgent } from "./framed-agent";

/** Access › Guardrails: the Zentinelle gate for this agent. */
export function SecureContent() {
  return <AgentSecureScreen agentName={useFramedAgent().agent.name} />;
}
