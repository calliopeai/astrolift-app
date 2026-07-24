import { AgentPillarPage } from "../components/agent-pillar-page";

export const metadata = { title: "Build · Agent · Astrolift" };

export default async function AgentBuildPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return <AgentPillarPage agentSlug={agentSlug} pillar="build" />;
}
