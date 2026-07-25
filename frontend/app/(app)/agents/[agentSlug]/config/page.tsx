import { ConfigEditorClient } from "@/app/(app)/apps/[slug]/config/config-editor-client";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Config · Agent · Astrolift" };

export default async function AgentConfigPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      <AgentAppSurface agentSlug={agentSlug}>
        <ConfigEditorClient slug={agentSlug} />
      </AgentAppSurface>
    </PreloadQuery>
  );
}
