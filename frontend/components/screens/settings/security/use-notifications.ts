"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { useGrowingLimit } from "@/components/feed/use-growing-limit";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  MARK_ALL_NOTIFICATIONS_READ,
  MARK_NOTIFICATION_READ,
} from "@/graphql/operations/operations.mutations";
import { LIST_MY_NOTIFICATIONS } from "@/graphql/operations/operations.queries";
import type { AstroliftNotification } from "@/graphql/operations/operations.types";

interface Resp {
  astroliftMyNotifications: AstroliftNotification[];
}

/**
 * The caller's notification inbox (polled) and the mark-read mutations.
 * The field takes a limit, not a cursor, so the feed asks for 100 more as
 * the reader nears the end of the newest 100. The data half of
 * NotificationsInbox.
 */
export function useNotifications() {
  const grow = useGrowingLimit(100);
  const { data, loading } = useQuery<Resp>(LIST_MY_NOTIFICATIONS, {
    variables: { unreadOnly: false, limit: grow.limit },
    pollInterval: 10000,
  });
  const notifications = data?.astroliftMyNotifications ?? [];

  const [markRead, { loading: marking }] = useMutation<{
    markNotificationRead: MutationResult<{ id: string; readAt: string | null }>;
  }>(MARK_NOTIFICATION_READ, {
    refetchQueries: [{ query: LIST_MY_NOTIFICATIONS }],
    awaitRefetchQueries: true,
  });

  const [markAll, { loading: markingAll }] = useMutation<{
    markAllNotificationsRead: { ok: boolean; data: { marked: number } | null };
  }>(MARK_ALL_NOTIFICATIONS_READ, {
    refetchQueries: [{ query: LIST_MY_NOTIFICATIONS }],
    awaitRefetchQueries: true,
  });

  async function onMarkAll() {
    const { data } = await markAll();
    if (data?.markAllNotificationsRead.ok) {
      toast.success(`Cleared ${data.markAllNotificationsRead.data?.marked ?? 0}`);
    }
  }

  async function onMarkRead(id: string) {
    await markRead({ variables: { input: { id } } });
  }

  return {
    notifications,
    loading,
    more: grow.feed(notifications.length, loading),
    marking,
    markingAll,
    onMarkRead,
    onMarkAll,
  };
}
