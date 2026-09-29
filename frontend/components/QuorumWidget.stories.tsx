import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { QuorumWidget } from "@/components/QuorumWidget";

const meta: Meta = { title: "Patterns/Approvals/QuorumWidget" };
export default meta;

const person = (name: string, approvedAt?: string) => ({
  userId: name,
  displayName: name,
  email: `${name}@example.com`,
  mailtoUrl: `mailto:${name}@example.com`,
  approvedAt,
});

export const Partial: StoryObj = {
  render: () => (
    <QuorumWidget
      requiredApproverCount={2}
      approvedBy={[person("leo", "2026-09-28T12:00:00Z")]}
      awaitingApprovers={[person("eric"), person("keith")]}
    />
  ),
};
export const Met: StoryObj = {
  render: () => (
    <QuorumWidget
      requiredApproverCount={2}
      approvedBy={[person("leo", "2026-09-28T12:00:00Z"), person("eric", "2026-09-28T12:03:00Z")]}
      awaitingApprovers={[]}
    />
  ),
};
