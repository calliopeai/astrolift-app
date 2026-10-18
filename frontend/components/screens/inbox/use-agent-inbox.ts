"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import {
  MARK_AGENT_MESSAGES_READ,
  REPLY_TO_AGENT_MESSAGE,
} from "@/graphql/agents/agent-messages.mutations";
import {
  AGENT_MESSAGES_UNREAD,
  LIST_AGENT_MESSAGE_THREADS,
  LIST_AGENT_MESSAGES,
} from "@/graphql/agents/agent-messages.queries";
import { ON_AGENT_MESSAGE_RECEIVED } from "@/graphql/agents/agent-messages.subscriptions";
import type {
  AgentMessage,
  AgentMessageReplyDraft,
  AgentMessageThread,
} from "@/graphql/agents/agent-messages.types";

import type { AgentInboxScreenProps } from "./AgentInboxScreen";

interface ThreadsResp {
  astroliftAgentMessageThreads: {
    unreadCount: number;
    nextCursor: string | null;
    items: AgentMessageThread[];
  };
}

interface MessagesResp {
  astroliftAgentMessages: {
    unreadCount: number;
    nextCursor: string | null;
    items: AgentMessage[];
  };
}

interface ReplyResp {
  replyToAgentMessage: { ok: boolean; errors: { message: string }[] | null };
}

const THREAD_PAGE = 25;
const MESSAGE_PAGE = 30;

/** The inbox page's data: threads, the open thread (``?thread=``), read and reply. */
export function useAgentInbox(): AgentInboxScreenProps {
  const router = useRouter();
  const pathname = usePathname() ?? "/inbox";
  const params = useSearchParams();
  const [unreadOnly, setUnreadOnly] = React.useState(false);
  const [replyingMessageId, setReplying] = React.useState<string | null>(null);
  const [replyError, setReplyError] = React.useState<string | null>(null);

  const threads = useQuery<ThreadsResp>(LIST_AGENT_MESSAGE_THREADS, {
    variables: { unreadOnly, after: null, first: THREAD_PAGE },
    fetchPolicy: "cache-and-network",
  });
  const items = threads.data?.astroliftAgentMessageThreads.items ?? [];
  const selectedThreadId = params?.get("thread") ?? items[0]?.threadId ?? null;

  const messages = useQuery<MessagesResp>(LIST_AGENT_MESSAGES, {
    variables: { threadId: selectedThreadId, after: null, first: MESSAGE_PAGE },
    skip: !selectedThreadId,
    fetchPolicy: "cache-and-network",
  });

  useSubscription(ON_AGENT_MESSAGE_RECEIVED, {
    onData: () => {
      void threads.refetch();
      if (selectedThreadId) void messages.refetch();
    },
  });

  const refreshAll = [
    {
      query: LIST_AGENT_MESSAGE_THREADS,
      variables: { unreadOnly, after: null, first: THREAD_PAGE },
    },
    { query: AGENT_MESSAGES_UNREAD },
  ];
  const [markRead] = useMutation(MARK_AGENT_MESSAGES_READ, { refetchQueries: refreshAll });
  const [reply] = useMutation<ReplyResp>(REPLY_TO_AGENT_MESSAGE, {
    refetchQueries: [
      ...refreshAll,
      {
        query: LIST_AGENT_MESSAGES,
        variables: { threadId: selectedThreadId, after: null, first: MESSAGE_PAGE },
      },
    ],
  });

  const threadsPage = threads.data?.astroliftAgentMessageThreads;
  const messagesPage = messages.data?.astroliftAgentMessages;

  return {
    threads: items,
    threadsLoading: threads.loading,
    threadsError: threads.error?.message ?? null,
    unreadCount: threadsPage?.unreadCount ?? 0,
    unreadOnly,
    onUnreadOnlyChange: setUnreadOnly,
    hasMoreThreads: Boolean(threadsPage?.nextCursor),
    onLoadMoreThreads: () => {
      void threads.fetchMore({
        variables: { after: threadsPage?.nextCursor },
        updateQuery: (prev, { fetchMoreResult }) => ({
          astroliftAgentMessageThreads: {
            ...fetchMoreResult.astroliftAgentMessageThreads,
            items: [
              ...prev.astroliftAgentMessageThreads.items,
              ...fetchMoreResult.astroliftAgentMessageThreads.items,
            ],
          },
        }),
      });
    },
    selectedThreadId,
    onSelectThread: (threadId) => {
      router.replace(`${pathname}?thread=${encodeURIComponent(threadId)}`);
    },
    // The page reads newest first; the thread shows oldest first.
    messages: [...(messagesPage?.items ?? [])].reverse(),
    messagesLoading: messages.loading,
    messagesError: messages.error?.message ?? null,
    hasEarlierMessages: Boolean(messagesPage?.nextCursor),
    onLoadEarlierMessages: () => {
      void messages.fetchMore({
        variables: { after: messagesPage?.nextCursor },
        updateQuery: (prev, { fetchMoreResult }) => ({
          astroliftAgentMessages: {
            ...fetchMoreResult.astroliftAgentMessages,
            items: [
              ...prev.astroliftAgentMessages.items,
              ...fetchMoreResult.astroliftAgentMessages.items,
            ],
          },
        }),
      });
    },
    onMarkThreadRead: (threadId) => {
      void markRead({ variables: { threadId } });
    },
    onReply: (messageId: string, draft: AgentMessageReplyDraft) => {
      setReplying(messageId);
      setReplyError(null);
      void reply({ variables: { input: { messageId, ...draft } } })
        .then(({ data }) => {
          const result = data?.replyToAgentMessage;
          if (result && !result.ok) setReplyError(result.errors?.[0]?.message ?? null);
        })
        .catch((error: Error) => setReplyError(error.message))
        .finally(() => setReplying(null));
    },
    replyingMessageId,
    replyError,
  };
}
