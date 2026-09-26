"use client";

import { ShieldCheckIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSignalWorkflowInstance } from "@/graphql/workflows/workflows.hooks";
import type { WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

/** What the stage before the gate produced: an agent's ``result`` when there is one. */
export function upstreamResult(
  gate: WorkflowStageExecution,
  executions: WorkflowStageExecution[]
): string {
  const previous = executions
    .filter((x) => x.stageOrder < gate.stageOrder)
    .sort((a, b) => b.stageOrder - a.stageOrder);
  const order = previous[0]?.stageOrder;
  const parts = previous
    .filter((x) => x.stageOrder === order)
    .map((x) => {
      const output = x.output as { result?: unknown } | null;
      const value =
        output && typeof output === "object" && "result" in output ? output.result : x.output;
      if (value == null) return "";
      return typeof value === "string" ? value : JSON.stringify(value, null, 2);
    })
    .filter(Boolean);
  return parts.join("\n\n---\n\n");
}

/**
 * Review a run's pending human gates (#1820): read what the previous stage
 * produced, then approve or reject with a note. The decision goes out as the
 * ``human_gate_decision`` signal keyed on the gate's integer ``executionId``
 * (a guid is silently dropped, #1786). Whether the caller may decide is the
 * server's call; a refusal comes back as the mutation error.
 */
export function GateReview({
  workflowId,
  executions,
  onDecided,
}: {
  workflowId: string | null;
  executions: WorkflowStageExecution[];
  onDecided?: () => void;
}) {
  const gates = executions.filter(
    (x) => x.stageKind === "human_gate" && x.humanGateState === "pending"
  );
  if (!workflowId || gates.length === 0) return null;
  return (
    <div className="grid gap-3">
      {gates.map((gate) => (
        <GateCard
          key={gate.guid}
          workflowId={workflowId}
          gate={gate}
          upstream={upstreamResult(gate, executions)}
          onDecided={onDecided}
        />
      ))}
    </div>
  );
}

function GateCard({
  workflowId,
  gate,
  upstream,
  onDecided,
}: {
  workflowId: string;
  gate: WorkflowStageExecution;
  upstream: string;
  onDecided?: () => void;
}) {
  const [signal, { loading }] = useSignalWorkflowInstance();
  const [note, setNote] = React.useState("");
  const [done, setDone] = React.useState("");

  async function decide(decision: "approved" | "rejected") {
    const { data } = await signal({
      variables: {
        workflowId,
        signalName: "human_gate_decision",
        payload: { execution_id: gate.executionId, decision, note },
      },
    });
    const result = data?.signalWorkflowInstance;
    if (result?.ok) {
      setDone(decision);
      toast.success(decision === "approved" ? "Approved" : "Rejected");
      onDecided?.();
    } else {
      toast.error(result?.errors?.[0]?.messages?.[0] ?? "The decision was not recorded");
    }
  }

  return (
    <section
      aria-label={`Gate ${gate.stageRole || gate.stageOrder}`}
      className="rounded-md border p-4"
    >
      <div className="mb-2 flex items-center gap-2 text-sm font-medium">
        <ShieldCheckIcon className="size-4" />
        Waiting for approval{gate.stageRole ? `: ${gate.stageRole}` : ""}
        {gate.stageApprovers && gate.stageApprovers.length > 0 && (
          <span className="text-muted-foreground font-normal">
            ({gate.stageApprovers.join(", ")})
          </span>
        )}
      </div>
      <pre className="bg-muted/40 mb-3 max-h-96 overflow-auto rounded p-3 text-xs whitespace-pre-wrap">
        {upstream || "The previous stage produced no output."}
      </pre>
      {done ? (
        <p className="text-muted-foreground text-sm">
          Decision sent: {done}. The run continues shortly.
        </p>
      ) : (
        <div className="grid gap-2">
          <Textarea
            aria-label="Decision note"
            placeholder="Note for the record (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="flex gap-2">
            <Button disabled={loading} onClick={() => decide("approved")}>
              Approve
            </Button>
            <Button variant="outline" disabled={loading} onClick={() => decide("rejected")}>
              Reject
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
