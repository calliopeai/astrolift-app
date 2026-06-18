import { redirect } from "next/navigation";

export default async function AgentDetailIndexPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  // /agents/[agentSlug] is the detail shell, not a destination — land on
  // Build, the default BROCS pillar (Run is stubbed until PR-10).
  const { agentSlug } = await params;
  redirect(`/agents/${encodeURIComponent(agentSlug)}/build`);
}
