"use client";

import { useWorkflowRunsTab } from "@/components/screens/workflows/detail/use-workflow-runs-tab";
import { WorkflowRunsTab } from "@/components/screens/workflows/detail/WorkflowRunsTab";

import { useFramedWorkflow } from "./framed-workflow";

/**
 * The Runs tab: this workflow's runs as the shared Runs list, embedded,
 * each opening its run page (`runs/[runId]`).
 */
export function RunsContent() {
  return <WorkflowRunsTab {...useWorkflowRunsTab(useFramedWorkflow())} />;
}
