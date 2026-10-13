import { routeTitle } from "@/lib/i18n/route-title";

import { RunAgentClient } from "./run-agent-client";

export const generateMetadata = routeTitle("runAgent");

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
