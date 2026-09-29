"use client";

import { GateReviewView } from "@/components/screens/workflows/detail/GateReview";
import { useGateReview } from "@/components/screens/workflows/detail/use-gate-review";
import type { WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

export { upstreamResult } from "@/components/screens/workflows/detail/GateReview";

/** A run's pending human gates (#1820); the view owns the markup. */
export function GateReview({
  workflowId,
  executions,
  onDecided,
}: {
  workflowId: string | null;
  executions: WorkflowStageExecution[];
  onDecided?: () => void;
}) {
  return <GateReviewView {...useGateReview({ workflowId, onDecided })} executions={executions} />;
}
