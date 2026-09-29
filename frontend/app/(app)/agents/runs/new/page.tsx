import { RunAgentClient } from "./run-agent-client";

export const metadata = { title: "Run agent · Astrolift" };

/**
 * Agents › Runs › Run agent: one run of any agent with inputs (the old
 * `/agents?tab=dispatch`). `?agent=<slug>` preselects the agent, as an
 * agent's Run with inputs links here.
 */
export default async function RunAgentPage({
  searchParams,
}: {
  searchParams: Promise<{ agent?: string | string[] }>;
}) {
  const { agent } = await searchParams;
  return <RunAgentClient agentSlug={(Array.isArray(agent) ? agent[0] : agent) ?? ""} />;
}
