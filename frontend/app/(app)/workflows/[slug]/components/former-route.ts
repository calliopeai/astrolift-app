import { redirect } from "next/navigation";

import {
  type SearchParams,
  workflowRedirectTarget,
} from "@/components/screens/workflows/detail/workflow-tabs-model";

/**
 * A route the workflow's tab row absorbed (spec 44 §5.2): it redirects
 * server-side to the tab that took it, keeping the slug and whatever query
 * the old link carried (`?run=`). The mapping is `WORKFLOW_FORMER_ROUTES`.
 */
export function formerWorkflowRoute(route: string) {
  return async function WorkflowFormerRoute({
    params,
    searchParams,
  }: {
    params: Promise<{ slug: string }>;
    searchParams: Promise<SearchParams>;
  }) {
    const { slug } = await params;
    redirect(workflowRedirectTarget(route, slug, await searchParams));
  };
}
