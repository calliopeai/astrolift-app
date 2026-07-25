import { AppTabs } from "@/app/(app)/apps/[slug]/components/app-tabs";
import { EnvironmentsClient } from "@/app/(app)/environments/environments-client";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Environments · Agent · Astrolift" };

export default async function AgentEnvironmentsPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: agentSlug }}>
      <AgentAppSurface agentSlug={agentSlug}>
        <EnvironmentsClient
          appSlug={agentSlug}
          tabs={<AppTabs slug={agentSlug} active="deployments" />}
        />
      </AgentAppSurface>
    </PreloadQuery>
  );
}
