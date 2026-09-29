import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { AppTabsView } from "./AppTabs";
import { LONG, TABS } from "./app-detail-shell.fixtures";

const meta: Meta<typeof AppTabsView> = {
  title: "Screens/Apps/Detail/AppTabs",
  component: AppTabsView,
  args: TABS,
};
export default meta;

type Story = StoryObj<typeof AppTabsView>;

/** Overview, the landing tab. The row has no loading, empty or error state. */
export const Overview: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

/** Logs & metrics, resolved from the pathname. */
export const LogsAndMetrics: Story = {
  args: { pathname: "/apps/checkout/logs" },
};

/** A former route's name as `active` on an unknown path lands on the tab that absorbed it. */
export const LegacyConsoleKey: Story = {
  args: { pathname: "/somewhere/else", active: "console" },
};

/** A nested detail lights up its tab. */
export const WorkloadDetail: Story = {
  args: { pathname: "/apps/checkout/workloads/web" },
};

/** Under the agent base path the links stay in agent context. */
export const AgentBasePath: Story = {
  args: { basePath: "/agents", pathname: "/agents/checkout/secrets" },
};

export const LongSlug: Story = {
  args: { slug: LONG, pathname: `/apps/${LONG}/domains` },
};

export const At768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <AppTabsView {...args} pathname="/apps/checkout/access" />
    </div>
  ),
};
