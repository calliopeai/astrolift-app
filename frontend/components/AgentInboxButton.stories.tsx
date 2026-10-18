import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentInboxButton } from "./AgentInboxButton";

const meta: Meta<typeof AgentInboxButton> = {
  title: "Shell/AgentInboxButton",
  component: AgentInboxButton,
  args: { unreadCount: 3 },
};
export default meta;

type Story = StoryObj<typeof AgentInboxButton>;

export const Unread: Story = {};

export const AllRead: Story = { args: { unreadCount: 0 } };

export const Many: Story = { args: { unreadCount: 240 } };
