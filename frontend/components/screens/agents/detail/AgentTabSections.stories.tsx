import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import { AgentTabSections } from "./AgentTabSections";

const body = (
  <EmptyState
    icon={<LayersIcon className="size-5" />}
    title="Section body"
    description="The section's own screen renders here."
  />
);

/**
 * The sections inside a consolidated agent tab (spec 44 §5.2). No data of
 * its own, so no loading, empty or error state; each section's body has them.
 */
const meta: Meta<typeof AgentTabSections> = {
  title: "Screens/Agents/Detail/AgentTabSections",
  component: AgentTabSections,
  parameters: { layout: "padded" },
  args: { slug: "support-bot", tab: "configuration", active: "build", children: body },
};
export default meta;

type Story = StoryObj<typeof AgentTabSections>;

/** Build, Run mode & triggers, Config editor, Manifest, CI / CD webhooks, Model access. */
export const Configuration: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Build" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(canvas.getByRole("link", { name: "Model access" })).toHaveAttribute(
      "href",
      "/agents/support-bot/configuration?section=model-access"
    );
  },
};

/** Live runs (the former Observe), Metrics (observability), Deploy history. */
export const LogsAndMetrics: Story = { args: { tab: "logs", active: "metrics" } };

/** Members, Deploy tokens, Security scans, Guardrails (the former Secure). */
export const Access: Story = { args: { tab: "access", active: "guardrails" } };

export const SkillsAndTools: Story = { args: { tab: "skills", active: "tools" } };

export const At768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <AgentTabSections {...args} />
    </div>
  ),
};
