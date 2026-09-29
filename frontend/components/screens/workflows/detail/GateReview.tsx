"use client";

import { ShieldCheckIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

import type { GateDecision, useGateReview } from "./use-gate-review";

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

export type GateReviewViewProps = ReturnType<typeof useGateReview> & {
  executions: WorkflowStageExecution[];
};

/**
 * Review a run's pending human gates (#1820): read what the previous stage
 * produced, then approve or reject with a note.
 */
export function GateReviewView({
  workflowId,
  executions,
  decide,
  decidingGuids,
}: GateReviewViewProps) {
  const gates = executions.filter(
    (x) => x.stageKind === "human_gate" && x.humanGateState === "pending"
  );
  if (!workflowId || gates.length === 0) return null;
  return (
    <div className="grid gap-3">
      {gates.map((gate) => (
        <GateCardView
          key={gate.guid}
          gate={gate}
          upstream={upstreamResult(gate, executions)}
          loading={decidingGuids.includes(gate.guid)}
          onDecide={(decision, note) => decide(gate, decision, note)}
        />
      ))}
    </div>
  );
}

/**
 * One pending gate: what the stage before it produced, then the decision.
 * With `canDecide` false (the viewer is not one of its approvers) the
 * decision is replaced by who it waits on; the run page sets it from the
 * gates the server says the viewer may decide.
 */
export function GateCardView({
  gate,
  upstream,
  loading,
  onDecide,
  canDecide = true,
}: {
  gate: WorkflowStageExecution;
  upstream: string;
  loading: boolean;
  onDecide: (decision: GateDecision, note: string) => Promise<boolean>;
  canDecide?: boolean;
}) {
  const [note, setNote] = React.useState("");
  const [done, setDone] = React.useState("");

  async function decide(decision: GateDecision) {
    if (await onDecide(decision, note)) setDone(decision);
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
      ) : !canDecide ? (
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
          Waiting on{" "}
          {gate.stageApprovers && gate.stageApprovers.length > 0
            ? gate.stageApprovers.join(", ")
            : "this gate's approvers"}
          . You are not one of them.
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
