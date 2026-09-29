import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { GateReviewView } from "./GateReview";
import { EXECUTIONS, GATES, LONG_EXECUTIONS } from "./workflow-detail-a.fixtures";

const meta: Meta<typeof GateReviewView> = {
  title: "Screens/Workflows/Detail/GateReview",
  component: GateReviewView,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof GateReviewView>;

/** One pending gate, the upstream draft shown above the decision. */
export const Full: Story = {
  args: GATES,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Decision note"), "ship it");
    await userEvent.click(canvas.getByRole("button", { name: "Approve" }));
    await expect(await canvas.findByText(/Decision sent: approved/)).toBeVisible();
  },
};

/** A decision in flight: both buttons disabled. There is no separate loading view. */
export const Loading: Story = { args: { ...GATES, decidingGuids: ["x-gate"] } };

/** No pending gate: the review renders nothing. */
export const Empty: Story = {
  args: { ...GATES, executions: EXECUTIONS.filter((x) => x.stageKind !== "human_gate") },
};

/** The server refused the decision: the form stays open for another try. */
export const DecisionRefused: Story = {
  args: { ...GATES, decide: async () => false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Reject" }));
    await expect(canvas.getByRole("button", { name: "Reject" })).toBeVisible();
  },
};

/** The stage before the gate produced nothing. */
export const NoUpstreamOutput: Story = {
  args: { ...GATES, executions: EXECUTIONS.filter((x) => x.stageKind === "human_gate") },
};

export const LongStrings: Story = { args: { ...GATES, executions: LONG_EXECUTIONS } };
