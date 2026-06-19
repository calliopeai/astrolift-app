import { AgentPillarPage } from "../components/agent-pillar-page";

export const metadata = { title: "Run · Agent · Astrolift" };

export default async function AgentRunPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return <AgentPillarPage agentSlug={agentSlug} pillar="run" />;
}
