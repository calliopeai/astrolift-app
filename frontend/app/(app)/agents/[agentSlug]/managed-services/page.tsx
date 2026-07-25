import { ManagedServicesClient } from "@/app/(app)/apps/[slug]/managed-services/managed-services-client";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Managed services · Agent · Astrolift" };

export default async function AgentManagedServicesPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
      <PreloadQuery
        query={LIST_MANAGED_SERVICES}
        variables={{ appSlug: agentSlug, environmentName: null }}
      >
        <AgentAppSurface agentSlug={agentSlug}>
          <ManagedServicesClient slug={agentSlug} />
        </AgentAppSurface>
      </PreloadQuery>
    </PreloadQuery>
  );
}
