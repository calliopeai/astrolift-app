import { ObservabilityClient } from "@/app/(app)/apps/[slug]/observability/observability-client";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Observability · Agent · Astrolift" };

export default async function AgentObservabilityPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      <PreloadQuery query={LIST_DEPLOYMENTS} variables={{ appSlug: agentSlug, limit: 50 }}>
        <PreloadQuery query={LIST_EVENTS} variables={{ limit: 200 }}>
          <AgentAppSurface agentSlug={agentSlug}>
            <ObservabilityClient slug={agentSlug} />
          </AgentAppSurface>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
