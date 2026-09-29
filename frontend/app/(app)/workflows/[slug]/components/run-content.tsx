"use client";

import { useWorkflowRun } from "@/components/screens/workflows/detail/use-workflow-run";
import { WorkflowRunScreen } from "@/components/screens/workflows/detail/WorkflowRunScreen";

import type { WorkflowDetailRenderProps } from "./workflow-detail-shell";

export { RunStateBadge } from "@/components/screens/workflows/detail/RunStateBadge";
export { isRunTerminal, latestRun } from "@/components/screens/workflows/detail/workflow-run-state";

/**
 * Run pillar container. The view lives in
 * components/screens/workflows/detail/WorkflowRunScreen.
 */
export function RunContent({ workflow, refetch }: WorkflowDetailRenderProps) {
  const state = useWorkflowRun(workflow, refetch);
  return <WorkflowRunScreen {...state} />;
}
