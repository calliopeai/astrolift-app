"use client";

import { WorkflowsListScreen } from "@/components/screens/workflows/list/WorkflowsListScreen";
import { useWorkflowsList } from "@/components/screens/workflows/list/use-workflows-list";

/** The workflows list. The screen owns the markup; the hook owns the data. */
export function WorkflowsClient() {
  return <WorkflowsListScreen {...useWorkflowsList()} />;
}
