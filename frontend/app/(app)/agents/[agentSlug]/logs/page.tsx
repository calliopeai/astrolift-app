import { AppDeploymentsClient } from "@/app/(app)/apps/[slug]/deployments/deployments-client";
import { ObservabilityClient } from "@/app/(app)/apps/[slug]/observability/observability-client";
import { metricsPanel } from "@/components/screens/apps/tools/metrics-panels";
import {
  activeAgentSection,
  type SearchParams,
} from "@/components/screens/agents/detail/agent-tabs-model";
import { AgentTabSections } from "@/components/screens/agents/detail/AgentTabSections";
import { LIST_DEPLOYMENTS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ObserveContent } from "../components/observe-content";

export const metadata = { title: "Logs & metrics · Agent · Astrolift" };

/**
 * The Logs & metrics tab (spec 44 §5.2, §9 step 5): Live runs (the former
 * Observe pillar), Metrics (the former observability page) and Deploy
 * history (the agent's deployments), one section at a time by `?section=`.
 * `/observe`, `/observability` and `/deployments` redirect here. Only the
 * active section mounts, and only its queries are preloaded.
 */
export default async function AgentLogsPage({
  params,
  searchParams,
}: {
  params: Promise<{ agentSlug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { agentSlug } = await params;
  const query = await searchParams;
  const section = activeAgentSection("logs", query);
  const panel = metricsPanel(
    typeof query.panel === "string" ? query.panel : query.pod ? "pods" : undefined
  );
  return (
    <AgentTabSections slug={agentSlug} tab="logs" active={section}>
      {section === "metrics" ? (
        <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
          <PreloadQuery query={LIST_DEPLOYMENTS} variables={{ appSlug: agentSlug, limit: 50 }}>
            <PreloadQuery query={LIST_EVENTS} variables={{ limit: 200 }}>
              <ObservabilityClient slug={agentSlug} panel={panel} />
            </PreloadQuery>
          </PreloadQuery>
        </PreloadQuery>
      ) : section === "deployments" ? (
        // The deployments page itself is keyed on the list's URL state, so
        // its client fetches it; the environments are the one fixed read.
        <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
          <AppDeploymentsClient slug={agentSlug} />
        </PreloadQuery>
      ) : (
        <ObserveContent />
      )}
    </AgentTabSections>
  );
}
