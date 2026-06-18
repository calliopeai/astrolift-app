import { AgentPillarPage } from "../components/agent-pillar-page";

export const metadata = { title: "Secure · Agent · Astrolift" };

export default async function AgentSecurePage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return <AgentPillarPage agentSlug={agentSlug} pillar="secure" />;
}
