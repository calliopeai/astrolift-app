import { AppTabs } from "@/app/(app)/apps/[slug]/components/app-tabs";
import { WebhooksClient } from "@/app/(app)/webhooks/webhooks-client";
import { LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Webhooks · Agent · Astrolift" };

export default async function AgentWebhooksPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={LIST_WEBHOOKS} variables={{ appSlug: agentSlug }}>
      <AgentAppSurface agentSlug={agentSlug}>
        <WebhooksClient
          appSlug={agentSlug}
          tabs={<AppTabs slug={agentSlug} active="settings" />}
        />
      </AgentAppSurface>
    </PreloadQuery>
  );
}
