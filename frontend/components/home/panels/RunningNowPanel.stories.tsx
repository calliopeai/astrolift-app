import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import {
  FAILED,
  LOADING,
  QUIET_SNAPSHOT,
  READY,
  RUNNING_LONG_SNAPSHOT,
  RUNNING_SNAPSHOT,
} from "./apps-agents.fixtures";
import { RunningNowPanelView } from "./RunningNowPanel";

/**
 * Running now: the agents with a run in flight in the viewer's own fleet
 * view (the switcher saves their choice), sized to the panel's column.
 */
const meta: Meta<typeof RunningNowPanelView> = {
  title: "Home/Panels/RunningNowPanel",
  component: RunningNowPanelView,
  args: {
    panel: HOME_PANELS["running-now"],
    ...READY,
    snapshot: RUNNING_SNAPSHOT,
    onSelectAgent: fn(),
  },
  decorators: [
    (Story) => (
      <PanelGrid>
        <Story />
      </PanelGrid>
    ),
  ],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof RunningNowPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("4 runs on 3 agents")).toBeInTheDocument();
    await expect(canvas.getByRole("radiogroup", { name: "View style" })).toBeInTheDocument();
  },
};

/** The same picture with motion off: the state is drawn still. */
export const ReducedMotion: Story = {
  render: (args) => (
    <div data-motion="reduced" className="contents">
      <RunningNowPanelView {...args} />
    </div>
  ),
};

export const Loading: Story = { args: { ...LOADING, snapshot: null } };

export const NothingRunning: Story = {
  args: { snapshot: QUIET_SNAPSHOT },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("Nothing running")).toBeInTheDocument();
  },
};

export const QueryError: Story = { args: { ...FAILED, snapshot: null } };

export const LongStrings: Story = { args: { snapshot: RUNNING_LONG_SNAPSHOT } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
