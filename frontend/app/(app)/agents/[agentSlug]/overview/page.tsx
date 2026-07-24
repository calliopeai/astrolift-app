import { AgentPillarPage } from "../components/agent-pillar-page";

export const metadata = { title: "Overview · Agent · Astrolift" };

export default async function AgentOverviewPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return <AgentPillarPage agentSlug={agentSlug} pillar="overview" />;
}
