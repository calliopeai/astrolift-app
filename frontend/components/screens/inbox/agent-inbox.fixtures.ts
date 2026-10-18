import type { AgentMessage, AgentMessageThread } from "@/graphql/agents/agent-messages.types";

import type { AgentInboxScreenProps } from "./AgentInboxScreen";

const RUN = "0192f0a1-7c2e-7b51-9d3a-5e4f6a7b8c9d";
const RUN_2 = "0192f0a1-7c2e-7b51-9d3a-5e4f6a7b8c9e";

function message(overrides: Partial<AgentMessage>): AgentMessage {
  return {
    id: "0192f0a1-0000-7000-8000-000000000001",
    threadId: RUN,
    kind: "update",
    source: "agent",
    title: "",
    body: "",
    options: [],
    attachments: [],
    agentSlug: "triage",
    agentName: "Triage",
    taskId: RUN,
    taskStatus: "running",
    replyToId: null,
    requestSequence: null,
    expiresAt: null,
    createdAt: "2026-10-17T09:00:00Z",
    deliveredAt: "2026-10-17T09:00:02Z",
    readAt: null,
    answerable: false,
    replies: [],
    ...overrides,
  };
}

export const UPDATE = message({
  id: "0192f0a1-0000-7000-8000-000000000001",
  title: "Halfway",
  body: "Indexed 40% of the repository. Two failing tests look related to the date parser.",
  readAt: "2026-10-17T09:01:00Z",
  attachments: [
    { kind: "url", ref: `/agents/runs/${RUN}`, label: "Run" },
    { kind: "citation", ref: "kb:doc-12", label: "Parser design note" },
  ],
});

export const QUESTION = message({
  id: "0192f0a1-0000-7000-8000-000000000002",
  kind: "question",
  title: "Which branch?",
  body: "I can open the fix against main or against the release branch. Which one?",
  options: [
    { id: "main", label: "main", description: "Ships next week" },
    { id: "release", label: "release/2026.10", description: "Ships today" },
  ],
  expiresAt: "2026-10-17T21:00:00Z",
  createdAt: "2026-10-17T09:05:00Z",
  answerable: true,
});

export const APPROVAL = message({
  id: "0192f0a1-0000-7000-8000-000000000003",
  kind: "approval",
  source: "platform",
  title: "Triage asks to use deploy",
  body: "Deploy the fix to staging.",
  options: [
    { id: "allow", label: "Allow", description: null },
    { id: "deny", label: "Deny", description: null },
  ],
  requestSequence: 7,
  createdAt: "2026-10-17T09:06:00Z",
  answerable: true,
});

export const ANSWERED = message({
  ...QUESTION,
  answerable: false,
  readAt: "2026-10-17T09:07:00Z",
  replies: [
    {
      id: "0192f0a1-0000-7000-8000-0000000000a1",
      messageId: QUESTION.id,
      authorLabel: "reba",
      body: "",
      choices: ["release"],
      decision: null,
      route: "live_run",
      deliveredAt: "2026-10-17T09:07:10Z",
      wakeTaskId: null,
      createdAt: "2026-10-17T09:07:00Z",
    },
  ],
});

export const RESULT = message({
  id: "0192f0a1-0000-7000-8000-000000000004",
  threadId: RUN_2,
  taskId: RUN_2,
  taskStatus: "completed",
  kind: "result",
  source: "platform",
  agentSlug: "nightly-report",
  agentName: "Nightly report",
  title: "Nightly report finished",
  body: "3 PRs merged, 1 flaky test quarantined.",
  createdAt: "2026-10-17T06:00:00Z",
});

function thread(
  last: AgentMessage,
  overrides: Partial<AgentMessageThread> = {}
): AgentMessageThread {
  return {
    threadId: last.threadId,
    agentSlug: last.agentSlug,
    agentName: last.agentName,
    taskId: last.taskId,
    taskStatus: last.taskStatus,
    lastMessage: last,
    lastMessageAt: last.createdAt,
    messageCount: 1,
    unreadCount: last.readAt ? 0 : 1,
    ...overrides,
  };
}

const noop = () => {};

export const INBOX: AgentInboxScreenProps = {
  threads: [thread(APPROVAL, { messageCount: 3, unreadCount: 2 }), thread(RESULT)],
  threadsLoading: false,
  threadsError: null,
  unreadCount: 3,
  unreadOnly: false,
  onUnreadOnlyChange: noop,
  hasMoreThreads: false,
  onLoadMoreThreads: noop,
  selectedThreadId: RUN,
  onSelectThread: noop,
  messages: [UPDATE, QUESTION, APPROVAL],
  messagesLoading: false,
  messagesError: null,
  hasEarlierMessages: false,
  onLoadEarlierMessages: noop,
  onMarkThreadRead: noop,
  onReply: noop,
  replyingMessageId: null,
  replyError: null,
};

const LONG = "A very long agent name that keeps going past the edge of the rail ".repeat(2);

export const LONG_THREAD = thread(
  message({
    id: "0192f0a1-0000-7000-8000-000000000009",
    threadId: "0192f0a1-7c2e-7b51-9d3a-5e4f6a7b8c90",
    agentName: LONG,
    title: "An update whose title is much longer than the card it sits in ".repeat(3),
    body: "Line one of a long body.\n".repeat(40),
  })
);
