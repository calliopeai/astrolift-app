import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import { AppTabSections } from "./AppTabSections";
import { LONG } from "./app-detail-shell.fixtures";

const body = (
  <EmptyState
    icon={<LayersIcon className="size-5" />}
    title="Section body"
    description="The section's own screen."
  />
);

const meta: Meta<typeof AppTabSections> = {
  title: "Screens/Apps/Detail/AppTabSections",
  component: AppTabSections,
  parameters: { layout: "padded" },
  args: { slug: "checkout", tab: "logs", active: "logs", children: body },
};
export default meta;

type Story = StoryObj<typeof AppTabSections>;

/** Logs & metrics: Logs, Metrics, Console, Commands. No loading or empty state of its own. */
export const LogsAndMetrics: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Metrics" })).toHaveAttribute(
      "href",
      "/apps/checkout/logs?section=metrics"
    );
    await expect(canvas.getByRole("link", { name: "Logs" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  },
};

export const Access: Story = { args: { tab: "access", active: "tokens" } };

export const Settings: Story = { args: { tab: "settings", active: "danger-zone" } };

/** Previews are a view of Deployments (`?view=previews`). */
export const DeploymentsPreviews: Story = { args: { tab: "deployments", active: "previews" } };

/** Scheduled jobs are a kind (`?kind=cronjob`); managed services a section. */
export const Workloads: Story = { args: { tab: "workloads", active: "jobs" } };

export const LongSlug: Story = { args: { slug: LONG, tab: "settings", active: "general" } };

export const At768: Story = {
  args: { tab: "settings", active: "configuration" },
  render: (args) => (
    <div style={{ width: 768 }}>
      <AppTabSections {...args} />
    </div>
  ),
};
