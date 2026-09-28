import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  LONG,
  LONG_AGENT,
  PAUSED_AGENT,
  PLATFORM_LINKS,
  RUNNING_AGENT,
  SHELL,
  TABS,
} from "./agent-detail-shell.fixtures";
import { AgentDetailShellView } from "./AgentDetailShell";
import { AgentTabsView } from "./AgentTabs";
import { AppPlatformLinksView } from "./AppPlatformLinks";

const meta: Meta<typeof AgentDetailShellView> = {
  title: "Screens/Agents/Detail/AgentDetailShell",
  component: AgentDetailShellView,
  parameters: { layout: "fullscreen" },
  args: {
    ...SHELL,
    tabs: <AgentTabsView {...TABS} pathname="/agents/research-scout/build" />,
    platformLinks: <AppPlatformLinksView {...PLATFORM_LINKS} />,
    children: (
      <div className="text-muted-foreground rounded-md border p-6 text-sm">
        Active pillar content renders here.
      </div>
    ),
  },
};
export default meta;

type Story = StoryObj<typeof AgentDetailShellView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Research Scout")).toBeInTheDocument();
    await expect(canvas.getByText("Task · Schedule")).toBeInTheDocument();
  },
};

export const Loading: Story = {
  args: { agent: null, loading: true },
};

/**
 * No agent with this slug. The shell has no separate empty or error state:
 * a failed or empty fleet read also lands here.
 */
export const NotFound: Story = {
  args: { agentSlug: "no-such-agent", agent: null, notFound: true },
};

export const Running: Story = {
  args: { agent: RUNNING_AGENT },
};

/** Paused, never run, and a repo with no link. */
export const PausedNeverRun: Story = {
  args: { agent: PAUSED_AGENT },
};

export const LongStrings: Story = {
  args: {
    agentSlug: LONG,
    agent: LONG_AGENT,
    tabs: <AgentTabsView agentSlug={LONG} pathname={`/agents/${LONG}/build`} />,
    platformLinks: <AppPlatformLinksView agentSlug={LONG} pathname={`/agents/${LONG}/build`} />,
  },
};
