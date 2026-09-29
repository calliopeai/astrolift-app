import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LONG, TABS } from "./workflow-detail-b.fixtures";
import { WorkflowTabsView } from "./WorkflowTabs";

const meta: Meta<typeof WorkflowTabsView> = {
  title: "Screens/Workflows/Detail/WorkflowTabs",
  component: WorkflowTabsView,
  args: TABS,
};
export default meta;

type Story = StoryObj<typeof WorkflowTabsView>;

/** Run is active. The bar has no loading, empty or error state. */
export const Run: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Run" })).toHaveAttribute("aria-current", "page");
  },
};

export const Observe: Story = {
  args: { pathname: "/workflows/nightly-sync/observe" },
};

/** An unknown path falls back to Build, the landing pillar. */
export const UnknownPath: Story = {
  args: { pathname: "/workflows/nightly-sync" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Build" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

export const LongSlug: Story = {
  args: { workflowSlug: LONG, pathname: `/workflows/${LONG}/observe` },
};
