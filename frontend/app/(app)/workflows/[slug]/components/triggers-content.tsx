"use client";

import { useWorkflowTriggers } from "@/components/screens/workflows/detail/use-workflow-triggers";
import { WorkflowTriggersView } from "@/components/screens/workflows/detail/WorkflowTriggers";

import { useFramedWorkflow } from "./framed-workflow";

/** The Triggers tab: how the workflow starts, and the enable toggle. */
export function TriggersContent() {
  const framed = useFramedWorkflow();
  const configured = framed.kind === "configured" ? framed : null;
  const state = useWorkflowTriggers(configured?.workflow ?? null, configured?.refetch ?? noop);
  return <WorkflowTriggersView {...state} />;
}

function noop() {}
