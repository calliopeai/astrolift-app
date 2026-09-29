import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import {
  AGENTS_NOTE,
  MY_AGENTS,
  MY_AGENTS_LONG,
  FAILED,
  LOADING,
  READY,
} from "./apps-agents.fixtures";
import { MyAgentsPanelView } from "./MyAgentsPanel";

/** My agents: running first, then the most recently run, with the note saying what Mine covers. */
const meta: Meta<typeof MyAgentsPanelView> = {
  title: "Home/Panels/MyAgentsPanel",
  component: MyAgentsPanelView,
  args: {
    panel: HOME_PANELS["my-agents"],
    ...READY,
    items: MY_AGENTS,
    count: 4,
    mineNote: AGENTS_NOTE,
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

type Story = StoryObj<typeof MyAgentsPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /View all/ })).toHaveAttribute(
      "href",
      HOME_PANELS["my-agents"].href
    );
  },
};

export const Loading: Story = { args: LOADING };

export const Empty: Story = { args: { items: [], count: 0, mineNote: AGENTS_NOTE } };

export const QueryError: Story = { args: { ...FAILED, items: [] } };

export const LongStrings: Story = { args: { items: MY_AGENTS_LONG, count: 1 } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
