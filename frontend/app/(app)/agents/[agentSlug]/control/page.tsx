import { AgentPillarPage } from "../components/agent-pillar-page";

export const metadata = { title: "Control · Agent · Astrolift" };

export default async function AgentControlPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return <AgentPillarPage agentSlug={agentSlug} pillar="control" />;
}
