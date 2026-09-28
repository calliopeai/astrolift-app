import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { GateReviewView } from "./GateReview";
import { DAG, GATES, LONG_DAG, PLANNED_DAG } from "./workflow-detail-a.fixtures";
import { WorkflowRunDagView } from "./WorkflowRunDag";

const meta: Meta<typeof WorkflowRunDagView> = {
  title: "Screens/Workflows/Detail/WorkflowRunDag",
  component: WorkflowRunDagView,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof WorkflowRunDagView>;

/** Fan-out children, the merge, and a pending gate under the graph. */
export const Full: Story = {
  args: { ...DAG, gateReview: <GateReviewView {...GATES} /> },
};

export const Loading: Story = { args: { ...DAG, loading: true, dagStages: [] } };

/** Nothing planned and nothing executed. */
export const Empty: Story = { args: { ...DAG, dagStages: [] } };

/** The planned skeleton before the engine reports any execution. */
export const Planned: Story = { args: PLANNED_DAG };

/**
 * The graph has no error state of its own: a failed query falls back to the
 * closest real view, the empty one. This shows a failed stage instead.
 */
export const FailedStage: Story = {
  args: {
    ...DAG,
    signature: "failed",
    dagStages: DAG.dagStages.map((s, i) => (i === 3 ? { ...s, status: "failed" } : s)),
  },
};

export const LongStrings: Story = { args: LONG_DAG };
