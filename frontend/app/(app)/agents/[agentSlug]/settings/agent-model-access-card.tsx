"use client";

import { useQuery } from "@apollo/client/react";

import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_AGENT_ENVIRONMENT_SPECS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentEnvironmentSpec } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { ManagedModelSection } from "../../managed-model-section";
import { VncSessionSection } from "../../vnc-session-section";

interface EnvSpecsResp {
  agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
}

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
 * wiring pattern, just a second spec-level property.
 */
export function AgentModelAccessCard({ agentSlug }: { agentSlug: string }) {
  // Reactive org id (matches useAgent): the cookie read races the post-render
  // effect that sets it, so gate the query on a resolved org.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const { data, loading } = useQuery<EnvSpecsResp>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });

  const spec = data?.agentEnvironmentSpecs?.find((s) => s.slug === agentSlug) ?? null;

  return (
    <>
      <Section
        title="Model access"
        description="How this agent authenticates to its model provider."
      >
        {spec ? (
          <ManagedModelSection envSpecSlug={spec.slug} managedModel={spec.managedModel} />
        ) : loading || !orgId ? (
          <Skeleton className="h-20 w-full" />
        ) : (
          <p className="text-muted-foreground text-sm italic">
            No environment spec registered for this agent.
          </p>
        )}
      </Section>
      <Section
        title="Live session"
        description="Whether this agent runs on a watchable VNC desktop or headless."
      >
        {spec ? (
          <VncSessionSection envSpecSlug={spec.slug} vncEnabled={spec.vncEnabled} />
        ) : loading || !orgId ? (
          <Skeleton className="h-20 w-full" />
        ) : (
          <p className="text-muted-foreground text-sm italic">
            No environment spec registered for this agent.
          </p>
        )}
      </Section>
    </>
  );
}
