import { redirect } from "next/navigation";

import { workflowsFormerTabTarget } from "@/components/screens/workflows/list/workflows-list";
import { routeTitle } from "@/lib/i18n/route-title";

import { WorkflowsClient } from "./workflows-client";

export const generateMetadata = routeTitle("workflows");

/**
 * Agents › Workflows: every workflow, one list (spec 44 §5.1). The old page's
 * `?tab=` panels moved where they belong (runs to Agents › Runs, templates to
 * the Templates view); an old link redirects there server-side
 * (WORKFLOWS_FORMER_TABS).
 */
export default async function WorkflowsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const target = workflowsFormerTabTarget(await searchParams);
  if (target) redirect(target);
  return <WorkflowsClient />;
}
