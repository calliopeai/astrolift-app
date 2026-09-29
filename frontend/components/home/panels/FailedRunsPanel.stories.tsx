import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import { FAILED_RUNS, FAILED_RUNS_LONG, FAILED, LOADING, READY } from "./apps-agents.fixtures";
import { FailedRunsPanelView } from "./FailedRunsPanel";

/** Failed runs: the newest failed agent and workflow runs with their reason. */
const meta: Meta<typeof FailedRunsPanelView> = {
  title: "Home/Panels/FailedRunsPanel",
  component: FailedRunsPanelView,
  args: { panel: HOME_PANELS["failed-runs"], ...READY, items: FAILED_RUNS, count: 12 },
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

type Story = StoryObj<typeof FailedRunsPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /View all/ })).toHaveAttribute(
      "href",
      HOME_PANELS["failed-runs"].href
    );
  },
};

export const Loading: Story = { args: LOADING };

export const Empty: Story = { args: { items: [], count: 0 } };

export const QueryError: Story = { args: { ...FAILED, items: [] } };

export const LongStrings: Story = { args: { items: FAILED_RUNS_LONG, count: null } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
