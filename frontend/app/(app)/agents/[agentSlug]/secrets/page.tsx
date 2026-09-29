import { SecretsContent } from "../components/secrets-content";

export const metadata = { title: "Secrets · Agent · Astrolift" };

/**
 * The Secrets tab (spec 44 §5.2): the agent's secret bindings as an
 * embedded list, and its reusable bundles, one section at a time by
 * `?section=`.
 */
export default async function AgentSecretsPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return <SecretsContent slug={agentSlug} />;
}
