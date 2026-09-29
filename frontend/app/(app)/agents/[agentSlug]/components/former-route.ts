import { redirect } from "next/navigation";

import {
  agentRedirectTarget,
  type SearchParams,
} from "@/components/screens/agents/detail/agent-tabs-model";

/**
 * A route the agent's tab row absorbed (spec 44 §5.2, §9 step 5): it
 * redirects server-side to the tab and section that took it, keeping the
 * slug and whatever query the old link carried. The mapping is
 * `AGENT_FORMER_ROUTES`.
 */
export function formerAgentRoute(route: string) {
  return async function AgentFormerRoute({
    params,
    searchParams,
  }: {
    params: Promise<{ agentSlug: string }>;
    searchParams: Promise<SearchParams>;
  }) {
    const { agentSlug } = await params;
    redirect(agentRedirectTarget(route, agentSlug, await searchParams));
  };
}
