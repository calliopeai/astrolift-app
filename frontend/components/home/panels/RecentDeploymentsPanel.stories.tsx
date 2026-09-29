import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import {
  RECENT_DEPLOYS,
  RECENT_DEPLOYS_LONG,
  FAILED,
  LOADING,
  READY,
} from "./apps-agents.fixtures";
import { RecentDeploymentsPanelView } from "./RecentDeploymentsPanel";

/** Recent deployments: the five newest deploys the viewer can see. */
const meta: Meta<typeof RecentDeploymentsPanelView> = {
  title: "Home/Panels/RecentDeploymentsPanel",
  component: RecentDeploymentsPanelView,
  args: { panel: HOME_PANELS["recent-deployments"], ...READY, items: RECENT_DEPLOYS, count: 214 },
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

type Story = StoryObj<typeof RecentDeploymentsPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /View all/ })).toHaveAttribute(
      "href",
      HOME_PANELS["recent-deployments"].href
    );
  },
};

export const Loading: Story = { args: LOADING };

export const Empty: Story = { args: { items: [], count: 0 } };

export const QueryError: Story = { args: { ...FAILED, items: [] } };

export const LongStrings: Story = { args: { items: RECENT_DEPLOYS_LONG, count: 1 } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
