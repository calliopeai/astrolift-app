import { redirect } from "next/navigation";

export default async function AgentDetailIndexPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  // /agents/[agentSlug] is the detail shell, not a destination — land on
  // Overview, the agent-native landing pillar.
  const { agentSlug } = await params;
  redirect(`/agents/${encodeURIComponent(agentSlug)}/overview`);
}
