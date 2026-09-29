import {
  activeAgentSection,
  type SearchParams,
} from "@/components/screens/agents/detail/agent-tabs-model";
import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentSettingsTabClient } from "./settings-client";

export const metadata = { title: "Settings · Agent · Astrolift" };

/**
 * The Settings tab (spec 44 §5.2, §5.3): General, Environments, Domains,
 * Managed services and one Danger zone, one section at a time by
 * `?section=`. `/environments`, `/domains` and `/managed-services` redirect
 * here. Only the active section's queries are preloaded.
 */
export default async function AgentSettingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ agentSlug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { agentSlug } = await params;
  const section = activeAgentSection("settings", await searchParams);
  const client = <AgentSettingsTabClient slug={agentSlug} />;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      {section === "environments" ? (
        <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
          {client}
        </PreloadQuery>
      ) : section === "domains" ? (
        <PreloadQuery query={LIST_APP_DOMAINS} variables={{ appSlug: agentSlug }}>
          <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
            {client}
          </PreloadQuery>
        </PreloadQuery>
      ) : section === "managed-services" ? (
        <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
          <PreloadQuery
            query={LIST_MANAGED_SERVICES}
            variables={{ appSlug: agentSlug, environmentName: null }}
          >
            {client}
          </PreloadQuery>
        </PreloadQuery>
      ) : (
        client
      )}
    </PreloadQuery>
  );
}
