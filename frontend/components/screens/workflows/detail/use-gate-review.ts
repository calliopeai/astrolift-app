"use client";

import * as React from "react";
import { toast } from "sonner";

import { useSignalWorkflowInstance } from "@/graphql/workflows/workflows.hooks";
import type { WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

export type GateDecision = "approved" | "rejected";

/**
 * Send a human gate decision (#1820) as the ``human_gate_decision`` signal
 * keyed on the gate's integer ``executionId`` (a guid is silently dropped,
 * #1786). Whether the caller may decide is the server's call; a refusal comes
 * back as the mutation error. Resolves true when the decision was recorded.
 */
export function useGateReview({
  workflowId,
  onDecided,
}: {
  workflowId: string | null;
  onDecided?: () => void;
}) {
  const [signal] = useSignalWorkflowInstance();
  const [decidingGuids, setDecidingGuids] = React.useState<string[]>([]);

  const decide = async (
    gate: WorkflowStageExecution,
    decision: GateDecision,
    note: string
  ): Promise<boolean> => {
    if (!workflowId) return false;
    setDecidingGuids((guids) => [...guids, gate.guid]);
    try {
      const { data } = await signal({
        variables: {
          workflowId,
          signalName: "human_gate_decision",
          payload: { execution_id: gate.executionId, decision, note },
        },
      });
      const result = data?.signalWorkflowInstance;
      if (result?.ok) {
        toast.success(decision === "approved" ? "Approved" : "Rejected");
        onDecided?.();
        return true;
      }
      toast.error(result?.errors?.[0]?.messages?.[0] ?? "The decision was not recorded");
      return false;
    } finally {
      setDecidingGuids((guids) => guids.filter((guid) => guid !== gate.guid));
    }
  };

  return { workflowId, decide, decidingGuids };
}
