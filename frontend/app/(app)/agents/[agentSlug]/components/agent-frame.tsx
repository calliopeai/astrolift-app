"use client";

import * as React from "react";

import { AppChromeProvider } from "@/app/(app)/apps/[slug]/components/app-chrome-context";
import { AgentFrame } from "@/components/screens/agents/detail/AgentFrame";
import { useAgentFrame } from "@/components/screens/agents/detail/use-agent-frame";

import { FramedAgentContext } from "./framed-agent";

/**
 * The frame around every `/agents/[agentSlug]/*` route: the header and the
 * one row of tabs (AgentFrame), with the route's own client below it. An
 * agent IS a RegisteredApp, so several tabs mount the shared `/apps/[slug]`
 * clients; the `framed` chrome drops their titles and their own tab rows, and
 * `basePath="/agents"` keeps their links in agent context.
 */
export function AgentFrameContainer({
  agentSlug,
  children,
}: {
  agentSlug: string;
  children: React.ReactNode;
}) {
  const { frame, agent, orgId } = useAgentFrame(agentSlug);
  const value = React.useMemo(() => (agent ? { agent, orgId } : null), [agent, orgId]);
  return (
    <AgentFrame {...frame}>
      <FramedAgentContext.Provider value={value}>
        <AppChromeProvider basePath="/agents" framed>
          {children}
        </AppChromeProvider>
      </FramedAgentContext.Provider>
    </AgentFrame>
  );
}
