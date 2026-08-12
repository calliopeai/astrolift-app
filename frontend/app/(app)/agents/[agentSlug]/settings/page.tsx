import { SettingsClient } from "@/app/(app)/apps/[slug]/settings/settings-client";
import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";
import { AgentModelAccessCard } from "./agent-model-access-card";

export const metadata = { title: "Settings · Agent · Astrolift" };

export default async function AgentSettingsPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
        <PreloadQuery query={LIST_APP_DOMAINS} variables={{ appSlug: agentSlug }}>
          <AgentAppSurface agentSlug={agentSlug}>
            {/* Agent-context-only Model access card, above the shared app
                settings content but inside the agent shell chrome. */}
            <AgentModelAccessCard agentSlug={agentSlug} />
            <SettingsClient slug={agentSlug} resourceKind="agent" />
          </AgentAppSurface>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
