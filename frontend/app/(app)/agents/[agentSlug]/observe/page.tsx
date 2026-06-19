import { AgentPillarPage } from "../components/agent-pillar-page";

export const metadata = { title: "Observe · Agent · Astrolift" };

export default async function AgentObservePage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return <AgentPillarPage agentSlug={agentSlug} pillar="observe" />;
}
