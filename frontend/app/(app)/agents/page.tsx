import { redirect } from "next/navigation";

import { agentsFormerTabTarget } from "@/components/screens/agents/list/agents-list";

import { AgentsClient } from "./agents-client";

export const metadata = { title: "Agents · Astrolift" };

/**
 * Agents › Agents: the registered agents, one list (spec 44 §5.1). The old
 * fleet page's `?tab=` panels moved to where they belong (Runs, Run agent,
 * Workloads); an old link redirects there server-side (AGENTS_FORMER_TABS).
 */
export default async function AgentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const target = agentsFormerTabTarget(await searchParams);
  if (target) redirect(target);
  return <AgentsClient />;
}
