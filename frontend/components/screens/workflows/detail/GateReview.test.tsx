import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowStageExecution } from "@/graphql/workflows/tiered.types";

import { GateCardView, GateReviewView, upstreamResult } from "./GateReview";
import { useGateReview } from "./use-gate-review";

/** The view on the real hook. */
function GateReview({
  workflowId,
  executions,
}: {
  workflowId: string | null;
  executions: WorkflowStageExecution[];
}) {
  return <GateReviewView {...useGateReview({ workflowId })} executions={executions} />;
}

const signal = vi
  .fn()
  .mockResolvedValue({ data: { signalWorkflowInstance: { ok: true, errors: [] } } });
vi.mock("@/graphql/workflows/workflows.hooks", () => ({
  useSignalWorkflowInstance: () => [signal, { loading: false }],
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function exec(overrides: Partial<WorkflowStageExecution>): WorkflowStageExecution {
  return {
    guid: "g",
    status: "completed",
    attemptNumber: 1,
    startedAt: null,
    endedAt: null,
    output: null,
    failure: null,
    errorMessage: "",
    createdAt: "",
    executionId: "1",
    stageGuid: "s",
    stageKind: "agent",
    stageOrder: 0,
    agentRunGuid: null,
    childWorkflowRunGuid: null,
    childWorkflowDefinitionSlug: null,
    childWorkflowStatus: null,
    ...overrides,
  };
}

const draft = exec({ guid: "a", stageOrder: 1, output: { result: "Draft reply to the thread" } });
const gate = exec({
  guid: "b",
  stageOrder: 2,
  stageKind: "human_gate",
  status: "running",
  executionId: "72",
  humanGateState: "pending",
  stageRole: "outreach review",
  stageApprovers: ["team:gtm"],
});

describe("upstreamResult", () => {
  it("reads the previous stage's agent result", () => {
    expect(
      upstreamResult(gate, [exec({ stageOrder: 0, output: { result: "old" } }), draft, gate])
    ).toBe("Draft reply to the thread");
  });
});

describe("GateReview", () => {
  it("shows the draft and approves with the gate's integer execution id", async () => {
    render(<GateReview workflowId="wf-1" executions={[draft, gate]} />);
    expect(screen.getByText("Draft reply to the thread")).toBeVisible();
    expect(screen.getByText(/team:gtm/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("Decision note"), { target: { value: "ship it" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByText(/Decision sent: approved/)).toBeVisible());
    expect(signal).toHaveBeenCalledWith({
      variables: {
        workflowId: "wf-1",
        signalName: "human_gate_decision",
        payload: { execution_id: "72", decision: "approved", note: "ship it" },
      },
    });
  });

  it("renders nothing without a pending gate", () => {
    const { container } = render(<GateReview workflowId="wf-1" executions={[draft]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows who a gate waits on, and no decision, to a viewer who may not decide", () => {
    const onDecide = vi.fn();
    render(
      <GateCardView
        gate={gate}
        upstream="Draft"
        loading={false}
        onDecide={onDecide}
        canDecide={false}
      />
    );
    expect(screen.getByText(/Waiting on team:gtm/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});
