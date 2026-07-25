import { AppDeployTokensClient } from "@/app/(app)/apps/[slug]/tokens/tokens-client";
import { LIST_APP_DEPLOY_TOKENS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Deploy tokens · Agent · Astrolift" };

export default async function AgentDeployTokensPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={LIST_APP_DEPLOY_TOKENS} variables={{ appSlug: agentSlug }}>
      <AgentAppSurface agentSlug={agentSlug}>
        <AppDeployTokensClient slug={agentSlug} />
      </AgentAppSurface>
    </PreloadQuery>
  );
}
