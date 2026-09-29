"use client";

import { useWorkflowRunPage } from "@/components/screens/workflows/detail/use-workflow-run-page";
import { WorkflowRunScreen } from "@/components/screens/workflows/detail/WorkflowRunScreen";

import { useFramedWorkflow } from "./framed-workflow";

/** One run of the framed workflow on the run archetype. */
export function RunContent({ slug, runId }: { slug: string; runId: string }) {
  return <WorkflowRunScreen {...useWorkflowRunPage(useFramedWorkflow(), slug, runId)} />;
}
