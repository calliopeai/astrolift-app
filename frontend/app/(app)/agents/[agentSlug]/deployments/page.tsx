import { AppDeploymentsClient } from "@/app/(app)/apps/[slug]/deployments/deployments-client";
import { LIST_ENVIRONMENTS, LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Deployments · Agent · Astrolift" };

export default async function AgentDeploymentsPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      <PreloadQuery query={LIST_DEPLOYMENTS} variables={{ appSlug: agentSlug, limit: 100 }}>
        <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
          <AgentAppSurface agentSlug={agentSlug}>
            <AppDeploymentsClient slug={agentSlug} />
          </AgentAppSurface>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
