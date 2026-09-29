"use client";

import { WorkflowRunDagView } from "@/components/screens/workflows/detail/WorkflowRunDag";
import { useWorkflowRunDag } from "@/components/screens/workflows/detail/use-workflow-run-dag";

import { GateReview } from "./gate-review";

export { buildRunDagStages } from "@/components/screens/workflows/detail/run-dag-stages";

/** Live run flow for a single workflow run (#1090); the view owns the markup. */
export function WorkflowRunDag(props: {
  workflowId: string | null;
  runId: string | null;
  definitionSlug: string;
  isTerminal: boolean;
}) {
  const dag = useWorkflowRunDag(props);
  return (
    <WorkflowRunDagView
      {...dag}
      gateReview={
        <GateReview
          workflowId={props.workflowId}
          executions={dag.executions}
          onDecided={() => void dag.refetch()}
        />
      }
    />
  );
}
