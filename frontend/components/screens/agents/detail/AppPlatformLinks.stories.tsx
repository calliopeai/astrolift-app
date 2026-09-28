import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LONG, PLATFORM_LINKS } from "./agent-detail-shell.fixtures";
import { AppPlatformLinksView } from "./AppPlatformLinks";

const meta: Meta<typeof AppPlatformLinksView> = {
  title: "Screens/Agents/Detail/AppPlatformLinks",
  component: AppPlatformLinksView,
  args: PLATFORM_LINKS,
};
export default meta;

type Story = StoryObj<typeof AppPlatformLinksView>;

/** Build's platform sub-pages. The row has no loading or error state. */
export const Build: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Config" })).toBeInTheDocument();
  },
};

/** On a platform sub-page, that link is highlighted. */
export const SecretsActive: Story = {
  args: { pathname: "/agents/research-scout/secrets" },
};

/** Control carries the most links. */
export const Control: Story = {
  args: { pathname: "/agents/research-scout/control" },
};

/**
 * Overview has no platform sub-pages, so the row renders nothing (the closest
 * thing to an empty state). A wrapper keeps the story canvas non-empty.
 */
export const OverviewRendersNothing: Story = {
  args: { pathname: "/agents/research-scout/overview" },
  render: (args) => (
    <div data-testid="wrapper">
      <AppPlatformLinksView {...args} />
    </div>
  ),
};

export const LongSlug: Story = {
  args: { agentSlug: LONG, pathname: `/agents/${LONG}/run` },
};
