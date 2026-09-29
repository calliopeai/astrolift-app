import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import { FAILING, FAILING_LONG, FAILED, LOADING, READY } from "./apps-agents.fixtures";
import { FailingPanelView } from "./FailingPanel";

/** Failing: failed deploys and runs, each row leading with its reason. */
const meta: Meta<typeof FailingPanelView> = {
  title: "Home/Panels/FailingPanel",
  component: FailingPanelView,
  args: { panel: HOME_PANELS["failing"], ...READY, items: FAILING, count: 7 },
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

type Story = StoryObj<typeof FailingPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /View all/ })).toHaveAttribute(
      "href",
      HOME_PANELS["failing"].href
    );
  },
};

export const Loading: Story = { args: LOADING };

export const Empty: Story = { args: { items: [], count: 0 } };

export const QueryError: Story = { args: { ...FAILED, items: [] } };

export const LongStrings: Story = { args: { items: FAILING_LONG, count: 2 } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
