import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { AgentActivityGraph } from "@/components/observability/AgentActivityGraph";

const meta: Meta = {
  title: "Patterns/Observability/AgentActivityGraph",
  parameters: { layout: "padded" },
};
export default meta;

const TOOLS = [
  { id: "t1", name: "search_orders" },
  { id: "t2", name: "refund" },
  { id: "t3", name: "send_email" },
];

export const Active: StoryObj = {
  render: () => (
    <AgentActivityGraph agentName="support-bot" tools={TOOLS} active runningCount={2} />
  ),
};
export const Idle: StoryObj = {
  render: () => (
    <AgentActivityGraph agentName="support-bot" tools={TOOLS} active={false} runningCount={0} />
  ),
};
