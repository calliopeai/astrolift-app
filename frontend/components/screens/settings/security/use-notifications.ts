"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
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
  const { data, loading, error, refetch } = useQuery<Resp>(LIST_MY_NOTIFICATIONS, {
    variables: { unreadOnly: false, limit: grow.limit },
    pollInterval: 10000,
    fetchPolicy: "cache-and-network",
  });
  const notifications = data?.astroliftMyNotifications ?? [];

  const [markRead, { loading: marking }] = useMutation<{
    markNotificationRead: MutationResult<{ id: string; readAt: string | null }>;
  }>(MARK_NOTIFICATION_READ, {
    refetchQueries: ["ListMyNotifications"],
    awaitRefetchQueries: true,
    onQueryUpdated: refetchAfterMutation,
  });

  const [markAll, { loading: markingAll }] = useMutation<{
    markAllNotificationsRead: { ok: boolean; data: { marked: number } | null };
  }>(MARK_ALL_NOTIFICATIONS_READ, {
    refetchQueries: ["ListMyNotifications"],
    awaitRefetchQueries: true,
    onQueryUpdated: refetchAfterMutation,
  });

  async function onMarkAll() {
    try {
      const { data } = await markAll();
      if (data?.markAllNotificationsRead.ok) {
        toast.success(`Cleared ${data.markAllNotificationsRead.data?.marked ?? 0}`);
      } else {
        toast.error("Could not mark notifications as read");
      }
    } catch {
      toast.error("Could not mark notifications as read");
    }
  }

  async function onMarkRead(id: string) {
    try {
      const result = await markRead({ variables: { input: { id } } });
      if (!result.data?.markNotificationRead.ok) toast.error("Could not mark notification as read");
    } catch {
      toast.error("Could not mark notification as read");
    }
  }

  return {
    notifications,
    loading,
    error: error && !data ? error.message : null,
    onRetry: () => {
      void refetch().catch(() => {});
    },
    more: grow.feed(notifications.length, loading),
    marking,
    markingAll,
    onMarkRead,
    onMarkAll,
  };
}
