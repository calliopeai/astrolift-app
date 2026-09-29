"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import {
  MARK_ALL_NOTIFICATIONS_READ,
  MARK_NOTIFICATION_READ,
} from "@/graphql/operations/operations.mutations";
import { LIST_MY_NOTIFICATIONS } from "@/graphql/operations/operations.queries";
import type { AstroliftNotification } from "@/graphql/operations/operations.types";

interface Resp {
  astroliftMyNotifications: AstroliftNotification[];
}

const POLL_MS = 10_000;

/** The signed-in person's notifications, polled, with the two read actions. */
export function useMyNotifications() {
  const { data, loading } = useQuery<Resp>(LIST_MY_NOTIFICATIONS, {
    variables: { unreadOnly: false, limit: 25 },
    pollInterval: POLL_MS,
  });

  const [markRead] = useMutation(MARK_NOTIFICATION_READ, {
    refetchQueries: [{ query: LIST_MY_NOTIFICATIONS }],
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

  return {
    notifications: data?.astroliftMyNotifications ?? [],
    loading,
    markingAll,
    onMarkRead: (id: string) => void markRead({ variables: { input: { id } } }),
    onMarkAll,
  };
}
