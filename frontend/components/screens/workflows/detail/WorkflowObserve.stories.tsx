import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { RunTimelinePanelView } from "./RunTimelinePanel";
import {
  LONG,
  LONG_INSTANCE_DETAIL,
  OBSERVE,
  RUN_RUNNING,
  TIMELINE,
} from "./workflow-detail-b.fixtures";
import { WorkflowObserveView } from "./WorkflowObserve";

const meta: Meta<typeof WorkflowObserveView> = {
  title: "Screens/Workflows/Detail/WorkflowObserve",
  component: WorkflowObserveView,
  args: {
    ...OBSERVE,
    timeline: <RunTimelinePanelView {...TIMELINE} />,
  },
};
export default meta;

type Story = StoryObj<typeof WorkflowObserveView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Engine timeline")).toBeInTheDocument();
    await expect(canvas.getByText("WorkflowExecutionStarted")).toBeInTheDocument();
  },
};

export const Loading: Story = {
  args: { runs: [], loading: true },
};

export const Empty: Story = {
  args: { runs: [] },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("No runs yet")).toBeInTheDocument();
  },
};

export const LoadFailed: Story = {
  args: { runs: [], error: { message: "Network error: failed to fetch" } },
};

export const LongStrings: Story = {
  args: {
    runs: [
      { ...RUN_RUNNING, temporalWorkflowId: `${LONG}-temporal-workflow-id` },
      ...OBSERVE.runs.slice(1),
    ],
    timeline: (
      <RunTimelinePanelView
        {...TIMELINE}
        run={{ ...RUN_RUNNING, temporalWorkflowId: `${LONG}-temporal-workflow-id` }}
        detail={LONG_INSTANCE_DETAIL}
      />
    ),
  },
};
