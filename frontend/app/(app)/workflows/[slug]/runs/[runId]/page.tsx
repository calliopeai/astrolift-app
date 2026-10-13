import { routeTitle } from "@/lib/i18n/route-title";

import { RunContent } from "../../components/run-page-content";

export const generateMetadata = routeTitle("run", "workflow");

/** One workflow run (spec 44 §5.5): its timeline, log, gates, workflow view and replay. */
export default async function WorkflowRunPage({
  params,
}: {
  params: Promise<{ slug: string; runId: string }>;
}) {
  const { slug, runId } = await params;
  return <RunContent slug={slug} runId={runId} />;
}
