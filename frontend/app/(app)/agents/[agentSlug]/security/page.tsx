import { AppSecurityClient } from "@/app/(app)/apps/[slug]/security/security-client";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Security · Agent · Astrolift" };

export default async function AgentSecurityPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      <PreloadQuery query={LIST_EVENTS} variables={{ limit: 100 }}>
        <AgentAppSurface agentSlug={agentSlug}>
          <AppSecurityClient slug={agentSlug} />
        </AgentAppSurface>
      </PreloadQuery>
    </PreloadQuery>
  );
}
