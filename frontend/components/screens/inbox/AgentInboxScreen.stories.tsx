import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ANSWERED, APPROVAL, INBOX, LONG_THREAD, RESULT, UPDATE } from "./agent-inbox.fixtures";
import { AgentInboxScreen } from "./AgentInboxScreen";

const meta: Meta<typeof AgentInboxScreen> = {
  title: "Screens/Inbox/AgentInbox",
  component: AgentInboxScreen,
  parameters: { layout: "fullscreen" },
  args: INBOX,
};
export default meta;

type Story = StoryObj<typeof AgentInboxScreen>;

/** A run waiting on a question and an approval. */
export const Full: Story = {};

export const Loading: Story = { args: { threads: [], threadsLoading: true, messages: [] } };

export const Empty: Story = { args: { threads: [], messages: [], unreadCount: 0 } };

export const EmptyUnread: Story = {
  args: { threads: [], messages: [], unreadCount: 0, unreadOnly: true },
};

export const QueryError: Story = {
  args: { threads: [], threadsError: "Could not load your inbox: network unreachable" },
};

export const NoThreadOpen: Story = { args: { selectedThreadId: null } };

export const ThreadLoading: Story = { args: { messages: [], messagesLoading: true } };

export const Answered: Story = {
  args: { messages: [UPDATE, ANSWERED, { ...APPROVAL, answerable: false }] },
};

export const Replying: Story = { args: { replyingMessageId: APPROVAL.id } };

export const ReplyRefused: Story = {
  args: { replyError: "Approving needs agent_task.send_input on this run." },
};

export const Finished: Story = {
  args: { selectedThreadId: RESULT.threadId, messages: [RESULT] },
};

export const MorePages: Story = { args: { hasMoreThreads: true, hasEarlierMessages: true } };

export const LongStrings: Story = {
  args: {
    threads: [LONG_THREAD, ...INBOX.threads],
    selectedThreadId: LONG_THREAD.threadId,
    messages: [LONG_THREAD.lastMessage],
  },
};

export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <AgentInboxScreen {...args} />
    </div>
  ),
};
