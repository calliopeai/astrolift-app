import { redirect } from "next/navigation";

import { workflowRunHref } from "@/components/screens/workflows/detail/workflow-run-model";

import { RunsContent } from "../components/runs-content";

export const metadata = { title: "Runs · Workflow · Astrolift" };

/**
 * The Runs tab: this workflow's runs (spec 44 §5.2); `/run` and `/observe`
 * redirect here. A link that still opens a run in `?run=` (the former
 * Observe pillar's) goes to that run's own page.
 */
export default async function WorkflowRunsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [{ slug }, { run }] = await Promise.all([params, searchParams]);
  if (typeof run === "string" && run) redirect(workflowRunHref(slug, run));
  return <RunsContent />;
}
