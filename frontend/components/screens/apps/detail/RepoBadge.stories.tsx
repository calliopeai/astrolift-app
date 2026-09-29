import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LONG, REPO_BADGE } from "./app-detail-shell.fixtures";
import { RepoBadge } from "./RepoBadge";

const meta: Meta<typeof RepoBadge> = {
  title: "Screens/Apps/Detail/RepoBadge",
  component: RepoBadge,
  args: REPO_BADGE,
};
export default meta;

type Story = StoryObj<typeof RepoBadge>;

/** GitHub mark, repo and branch. The badge has no loading or error state. */
export const GitHub: Story = {};

/** A self-hosted provider with no `sourceRepo`: the URL path stands in, no branch. */
export const SelfHostedNoBranch: Story = {
  args: {
    sourceKind: "gitea",
    sourceUrl: "https://git.acme.internal/platform/checkout.git",
    sourceRepo: "",
    branch: "",
  },
};

/** No source URL renders nothing; the wrapper is here so the story has a canvas. */
export const NoSource: Story = {
  args: { sourceUrl: null },
  render: (args) => (
    <div data-testid="repo-badge-empty">
      <RepoBadge {...args} />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryByRole("link")).toBeNull();
  },
};

export const LongStrings: Story = {
  args: { sourceRepo: `acme/${LONG}`, branch: `feature/${LONG}` },
};
