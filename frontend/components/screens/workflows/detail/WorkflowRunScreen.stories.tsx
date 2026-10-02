import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  ARN,
  FAILED_EXECUTIONS,
  FAILED_RUN,
  LONG_EXECUTIONS,
  LONG_PLAN,
  LOOP_EXECUTIONS,
  LOOP_PLAN,
  LOOP_RUN,
  RUN,
  RUN_SCREEN,
  SHA,
} from "./workflow-run.fixtures";
import { WorkflowRunScreen } from "./WorkflowRunScreen";

/** One workflow run on the run archetype (spec 44 §5.5). */
const meta: Meta<typeof WorkflowRunScreen> = {
  title: "Screens/Workflows/Detail/WorkflowRunScreen",
  component: WorkflowRunScreen,
  parameters: { layout: "padded" },
  args: RUN_SCREEN,
};
export default meta;

type Story = StoryObj<typeof WorkflowRunScreen>;

/** A fan-out merged and now waits at a gate the viewer may decide: Approve leads the header. */
export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByText("run run-8").length).toBeGreaterThan(0);
    await expect(canvas.getAllByRole("button", { name: "Approve" }).length).toBe(2);
    await expect(canvas.getByLabelText("Decision note")).toBeInTheDocument();
    await expect(canvas.getByText(/3 of 3 branches settled/)).toBeInTheDocument();
  },
};

/** The gate waits on someone else: no decision, and Stop is the primary action. */
export const GateNotYours: Story = {
  args: { viewerGateIds: [] },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: "Approve" })).toBeNull();
    await expect(canvas.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    await expect(canvas.getByText(/You are not one of them/)).toBeInTheDocument();
  },
};

/** Test failed and sent the work back to Code: two rounds, each with its cause. */
export const Looped: Story = {
  args: {
    runId: LOOP_RUN.guid,
    run: LOOP_RUN,
    plan: LOOP_PLAN,
    executions: LOOP_EXECUTIONS,
    history: [],
    viewerGateIds: [],
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByText("Round 2 of 5").length).toBeGreaterThan(0);
    await expect(
      canvas.getAllByText(/tester failed: 3 specs failed in checkout/).length
    ).toBeGreaterThan(0);
  },
};

/** One branch failed: the reason leads the timeline, the rest never ran. */
export const FailedBranch: Story = {
  args: {
    run: FAILED_RUN,
    runId: FAILED_RUN.guid,
    executions: FAILED_EXECUTIONS,
    viewerGateIds: [],
  },
};

/** Started, but no stage has reported yet: the plan shows pending. */
export const NoStagesYet: Story = {
  args: { executions: [], history: [] },
};

/** The run never reached the engine: nothing to graph or replay. */
export const NotReachedEngine: Story = {
  args: {
    run: {
      ...RUN,
      temporalWorkflowId: null,
      temporalRunId: null,
      live: false,
      outcome: "failed",
      status: "failed",
    },
    executions: [],
    history: [],
  },
};

export const Loading: Story = { args: { run: null, loading: true } };

export const NotFound: Story = { args: { run: null } };

export const LoadError: Story = {
  args: { run: null, error: { message: "Response not successful: Received status code 502" } },
};

export const StagesFailedToLoad: Story = {
  args: { executions: [], executionsError: "Temporal frontend unavailable", history: [] },
};

export const LongStrings: Story = {
  args: {
    workflowName: `Outbound pipeline for ${ARN}`,
    runId: SHA,
    run: { ...RUN, guid: SHA, temporalWorkflowId: `${ARN}-temporal-workflow-id` },
    plan: LONG_PLAN,
    executions: LONG_EXECUTIONS,
    viewerGateIds: ["73"],
  },
};

export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <WorkflowRunScreen {...args} />
    </div>
  ),
};
