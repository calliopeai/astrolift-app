"use client";

import type * as React from "react";

import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";

import type { useAgentModelAccess } from "./use-agent-model-access";

export type AgentModelAccessViewProps = ReturnType<typeof useAgentModelAccess> & {
  /** The managed-model toggle for the resolved spec (`ManagedModelSection`). */
  managedModel?: React.ReactNode;
  /** The VNC-vs-headless toggle for the resolved spec (`VncSessionSection`). */
  vncSession?: React.ReactNode;
};

/**
 * Agent-context-only "Model access" and "Live session" sections for
 * `/agents/[agentSlug]/settings`. The two toggles are slots, rendered once the
 * agent's environment spec has resolved.
 */
export function AgentModelAccessView({
  spec,
  loading,
  orgId,
  managedModel,
  vncSession,
}: AgentModelAccessViewProps) {
  const fallback =
    loading || !orgId ? (
      <Skeleton className="h-20 w-full" />
    ) : (
      <p className="text-muted-foreground text-sm italic">
        No environment spec registered for this agent.
      </p>
    );

  return (
    <>
      <Section
        title="Model access"
        description="How this agent authenticates to its model provider."
      >
        {spec ? managedModel : fallback}
      </Section>
      <Section
        title="Live session"
        description="Whether this agent runs on a watchable VNC desktop or headless."
      >
        {spec ? vncSession : fallback}
      </Section>
    </>
  );
}
