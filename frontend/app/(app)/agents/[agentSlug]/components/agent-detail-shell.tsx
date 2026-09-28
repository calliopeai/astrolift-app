"use client";

import * as React from "react";

import { AgentDetailShellView } from "@/components/screens/agents/detail/AgentDetailShell";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

import { AgentTabs } from "./agent-tabs";
import { AppPlatformLinks } from "./app-platform-links";
import { useAgent } from "./use-agent";

export interface AgentDetailRenderProps {
  agent: AstroliftAgentListItem;
  orgId: string;
}

interface AgentDetailShellProps {
  agentSlug: string;
  /** Renders the active tab's content once the agent has resolved. */
  children: (props: AgentDetailRenderProps) => React.ReactNode;
}

/**
 * Shared chrome for every `/agents/[agentSlug]/<pillar>` page: resolves the
 * agent and renders the identity header, the BROCS `AgentTabs`, then slots the
 * active tab's content. Mirrors how `AppDetailClient` wraps the app-detail
 * surface, but as a render-prop shell so each pillar route owns only its own
 * content. The view lives in components/screens/agents/detail/AgentDetailShell.
 */
export function AgentDetailShell({ agentSlug, children }: AgentDetailShellProps) {
  const { agent, loading, notFound, orgId } = useAgent(agentSlug);
  const resolved = !loading && !notFound && agent ? agent : null;

  return (
    <AgentDetailShellView
      agentSlug={agentSlug}
      agent={agent}
      loading={loading}
      notFound={notFound}
      tabs={resolved ? <AgentTabs agentSlug={resolved.slug} /> : null}
      // An agent IS a RegisteredApp — its platform sub-pages (config/CI-CD,
      // settings, security, secrets, tokens, deployments…) live at
      // /apps/<slug>/* and were otherwise unreachable from the agent surface.
      // Surface them as a secondary row scoped to the active pillar (same
      // seg→pillar grouping as the app surface), beneath the BROCS pillar bar
      // above. Uses agent.slug — the [agentSlug] route param every agent page
      // resolves against — so the links stay on valid agent routes.
      platformLinks={resolved ? <AppPlatformLinks agentSlug={resolved.slug} /> : null}
    >
      {resolved ? children({ agent: resolved, orgId }) : null}
    </AgentDetailShellView>
  );
}
