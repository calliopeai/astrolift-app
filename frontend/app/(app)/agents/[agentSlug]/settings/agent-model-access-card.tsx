"use client";

import { AgentModelAccessView } from "@/components/screens/agents/detail/AgentModelAccess";
import { useAgentModelAccess } from "@/components/screens/agents/detail/use-agent-model-access";

import { ManagedModelSection } from "../../managed-model-section";
import { VncSessionSection } from "../../vnc-session-section";

/**
 * Agent-context-only "Model access" card for `/agents/[agentSlug]/settings`.
 *
 * Surfaces the managed-model toggle (cluster-native model provider vs API key)
 * for the agent's environment spec — the spec slug matches the agent slug
 * (agent `foo` ↔ spec `foo`). Mounted ONLY by the agent settings route, never
 * by the shared `/apps` settings client, so the plain app settings page never
 * gains this control. The toggle itself is the shared {@link ManagedModelSection},
 * identical in look + wiring to the one in the agent secrets dialog.
 *
 * A sibling "Live session" Section carries the {@link VncSessionSection} toggle
 * (watchable VNC vs headless) for the same env spec — same resolution, same
 * wiring pattern, just a second spec-level property. The view lives in
 * components/screens/agents/detail/AgentModelAccess.
 */
export function AgentModelAccessCard({ agentSlug }: { agentSlug: string }) {
  const access = useAgentModelAccess(agentSlug);
  const { spec } = access;
  return (
    <AgentModelAccessView
      {...access}
      managedModel={
        spec ? (
          <ManagedModelSection envSpecSlug={spec.slug} managedModel={spec.managedModel} />
        ) : null
      }
      vncSession={
        spec ? <VncSessionSection envSpecSlug={spec.slug} vncEnabled={spec.vncEnabled} /> : null
      }
    />
  );
}
