import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { StatusBadge, SummaryCard } from "./ReviewParts";

const meta: Meta<typeof StatusBadge> = {
  title: "Wizard/ReviewParts",
  component: StatusBadge,
};
export default meta;
type Story = StoryObj<typeof StatusBadge>;
export const Pending: Story = { args: { status: "pending" } };
export const Running: Story = { args: { status: "running" } };
export const Done: Story = { args: { status: "done" } };
export const Failed: Story = {
  args: { status: "failed", error: "Access was revoked. Review the selected project." },
};
export const Skipped: Story = { args: { status: "skipped" } };
export const LongSummary: Story = {
  render: () => (
    <SummaryCard
      step={1}
      title="Repository"
      onJump={() => {}}
      rows={[
        {
          label: "Source",
          value: "organization-with-a-long-name/repository-with-a-long-name-and-agent-manifests",
          mono: true,
        },
      ]}
    />
  ),
};
