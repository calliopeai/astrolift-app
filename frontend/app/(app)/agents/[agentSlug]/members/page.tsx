import { AppMembersClient } from "@/app/(app)/apps/[slug]/members/app-members-client";
import { LIST_ROLES, LIST_ROLE_BINDINGS } from "@/graphql/identity/identity.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Members · Agent · Astrolift" };

export default async function AgentMembersPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      <PreloadQuery query={LIST_ROLE_BINDINGS}>
        <PreloadQuery query={LIST_ROLES}>
          <AgentAppSurface agentSlug={agentSlug}>
            <AppMembersClient slug={agentSlug} />
          </AgentAppSurface>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
