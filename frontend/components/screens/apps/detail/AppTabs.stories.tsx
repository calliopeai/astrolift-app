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

/** Run › Overview, the landing tab. The bar has no loading, empty or error state. */
export const Overview: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

/** Observe › Logs, resolved from the pathname. */
export const ObserveLogs: Story = {
  args: { pathname: "/apps/checkout/logs" },
};

/** A legacy `active="console"` key on an unknown path lands on Observe › Logs. */
export const LegacyConsoleKey: Story = {
  args: { pathname: "/apps/checkout/console", active: "console" },
};

/** Under the agent base path the links stay in agent context. */
export const AgentBasePath: Story = {
  args: { basePath: "/agents", pathname: "/agents/checkout/secrets" },
};

export const LongSlug: Story = {
  args: { slug: LONG, pathname: `/apps/${LONG}/domains` },
};
