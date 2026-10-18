"use client";

import { useQuery, useSubscription } from "@apollo/client/react";

import { AGENT_MESSAGES_UNREAD } from "@/graphql/agents/agent-messages.queries";
import { ON_AGENT_MESSAGE_RECEIVED } from "@/graphql/agents/agent-messages.subscriptions";

interface Resp {
  astroliftAgentMessages: { unreadCount: number };
}

const POLL_MS = 60_000;

/** The shell's unread agent-message count: live from the subscription, polled as a fallback. */
export function useAgentInboxUnread() {
  const { data, refetch } = useQuery<Resp>(AGENT_MESSAGES_UNREAD, {
    fetchPolicy: "cache-and-network",
    pollInterval: POLL_MS,
  });
  useSubscription(ON_AGENT_MESSAGE_RECEIVED, {
    onData: () => {
      void refetch();
    },
  });
  return { unreadCount: data?.astroliftAgentMessages.unreadCount ?? 0 };
}
