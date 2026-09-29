import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import { MINE_NOTE, MY_APPS, MY_APPS_LONG, FAILED, LOADING, READY } from "./apps-agents.fixtures";
import { MyAppsPanelView } from "./MyAppsPanel";

/** My apps: the viewer's apps with their latest deploy, and the note saying what Mine covers while it is a stand-in. */
const meta: Meta<typeof MyAppsPanelView> = {
  title: "Home/Panels/MyAppsPanel",
  component: MyAppsPanelView,
  args: { panel: HOME_PANELS["my-apps"], ...READY, items: MY_APPS, count: 9, mineNote: MINE_NOTE },
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

type Story = StoryObj<typeof MyAppsPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /View all/ })).toHaveAttribute(
      "href",
      HOME_PANELS["my-apps"].href
    );
    await expect(canvas.getByText(MINE_NOTE)).toBeInTheDocument();
  },
};

export const Loading: Story = { args: LOADING };

export const Empty: Story = { args: { items: [], count: 0 } };

export const QueryError: Story = { args: { ...FAILED, items: [] } };

export const LongStrings: Story = { args: { items: MY_APPS_LONG, count: 1 } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
