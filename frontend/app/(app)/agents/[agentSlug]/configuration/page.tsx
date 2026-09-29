import {
  activeAgentSection,
  type SearchParams,
} from "@/components/screens/agents/detail/agent-tabs-model";
import { LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ConfigurationContent } from "../components/configuration-content";

export const metadata = { title: "Configuration · Agent · Astrolift" };

/**
 * The Configuration tab (spec 44 §5.2, §5.3): Build, Run mode & triggers,
 * the config editor, Manifest, CI / CD webhooks and Model access on the
 * settings archetype, one section at a time by `?section=`. `/build`,
 * `/control`, `/config`, `/manifest` and `/webhooks` redirect here. Only the
 * active section mounts, and only its queries are preloaded.
 *
 * Manifest has no `PreloadQuery`: its client fetches both queries itself
 * (more streams hang the dev-mode Suspense boundary in Next.js 16).
 */
export default async function AgentConfigurationPage({
  params,
  searchParams,
}: {
  params: Promise<{ agentSlug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { agentSlug } = await params;
  const section = activeAgentSection("configuration", await searchParams);
  const content = <ConfigurationContent slug={agentSlug} />;
  return section === "config" ? (
    <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
      {content}
    </PreloadQuery>
  ) : section === "webhooks" ? (
    <PreloadQuery query={LIST_WEBHOOKS} variables={{ appSlug: agentSlug }}>
      {content}
    </PreloadQuery>
  ) : (
    content
  );
}
