import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { RunTimelinePanelView } from "./RunTimelinePanel";
import {
  INSTANCE_DETAIL,
  LONG,
  LONG_INSTANCE_DETAIL,
  RUN_FAILED,
  RUN_RUNNING,
  TIMELINE,
} from "./workflow-detail-b.fixtures";

const meta: Meta<typeof RunTimelinePanelView> = {
  title: "Screens/Workflows/Detail/RunTimelinePanel",
  component: RunTimelinePanelView,
  args: {
    ...TIMELINE,
    dag: (
      <div className="text-muted-foreground rounded-md border p-6 text-sm">
        The live stage DAG renders here.
      </div>
    ),
  },
};
export default meta;

type Story = StoryObj<typeof RunTimelinePanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("retry 2")).toBeInTheDocument();
  },
};

export const Loading: Story = {
  args: { detail: null, loading: true },
};

/** A run with no engine instance: the panel's empty state. */
export const NoEngineInstance: Story = {
  args: { run: RUN_FAILED, detail: null },
};

export const NoHistory: Story = {
  args: { detail: { ...INSTANCE_DETAIL, history: [] } },
};

export const LoadFailed: Story = {
  args: { detail: null, error: { message: "Temporal frontend unavailable" } },
};

/** Nothing selected; the hint only shows at md and wider. */
export const NoRunSelected: Story = {
  args: { run: null, detail: null },
};

export const LongStrings: Story = {
  args: {
    run: { ...RUN_RUNNING, temporalWorkflowId: `${LONG}-temporal-workflow-id` },
    detail: LONG_INSTANCE_DETAIL,
  },
};
