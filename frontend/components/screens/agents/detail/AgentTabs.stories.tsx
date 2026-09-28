import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LONG, TABS } from "./agent-detail-shell.fixtures";
import { AgentTabsView } from "./AgentTabs";

const meta: Meta<typeof AgentTabsView> = {
  title: "Screens/Agents/Detail/AgentTabs",
  component: AgentTabsView,
  args: TABS,
};
export default meta;

type Story = StoryObj<typeof AgentTabsView>;

/** Overview, the landing pillar. The bar has no loading, empty or error state. */
export const Overview: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

/** A platform sub-page (`…/config`) lights up its owning pillar, Build. */
export const PlatformPageLightsBuild: Story = {
  args: { pathname: "/agents/research-scout/config" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Build" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

/** An unknown path falls back to Overview. */
export const UnknownPath: Story = {
  args: { pathname: "/agents/research-scout" },
};

export const LongSlug: Story = {
  args: { agentSlug: LONG, pathname: `/agents/${LONG}/secure` },
};
