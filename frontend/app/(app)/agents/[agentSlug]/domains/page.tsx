import { AppDomainsClient } from "@/app/(app)/apps/[slug]/domains/domains-client";
import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Domains · Agent · Astrolift" };

export default async function AgentDomainsPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={LIST_APP_DOMAINS} variables={{ appSlug: agentSlug }}>
      <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
        <AgentAppSurface agentSlug={agentSlug}>
          <AppDomainsClient slug={agentSlug} />
        </AgentAppSurface>
      </PreloadQuery>
    </PreloadQuery>
  );
}
