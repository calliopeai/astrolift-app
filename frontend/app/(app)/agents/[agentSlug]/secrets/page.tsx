import { SecretsClient } from "@/app/(app)/apps/[slug]/secrets/secrets-client";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_APP_SECRETS } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Secrets · Agent · Astrolift" };

export default async function AgentSecretsPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
      <PreloadQuery
        query={LIST_APP_SECRETS}
        variables={{ appSlug: agentSlug, environmentName: null }}
      >
        <AgentAppSurface agentSlug={agentSlug}>
          <SecretsClient slug={agentSlug} />
        </AgentAppSurface>
      </PreloadQuery>
    </PreloadQuery>
  );
}
