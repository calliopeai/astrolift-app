"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { BellIcon, BellOffIcon, CheckCheckIcon } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
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

export function NotificationsClient() {
  const { data, loading } = useQuery<Resp>(LIST_MY_NOTIFICATIONS, {
    variables: { unreadOnly: false, limit: 100 },
    pollInterval: 10000,
  });

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

  async function handleMarkAll() {
    const { data } = await markAll();
    if (data?.markAllNotificationsRead.ok) {
      toast.success(`Cleared ${data.markAllNotificationsRead.data?.marked ?? 0}`);
    }
  }

  const list = data?.astroliftMyNotifications ?? [];
  const unread = list.filter((n) => !n.readAt);

  return (
    <PageShell
      title="Notifications"
      description="Your inbox: deploy approvals, failure alerts, invitations, quota warnings."
    >
      <div className="flex items-center justify-between gap-3">
        <Badge variant="outline" className="gap-1">
          <BellIcon className="size-3" />
          {unread.length} unread
        </Badge>
        {unread.length > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={handleMarkAll}
            disabled={markingAll}
          >
            <CheckCheckIcon className="size-4" />
            Mark all as read
          </Button>
        )}
      </div>

      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BellOffIcon className="size-5" />}
                title="Inbox empty"
                description="Notifications appear here as deploys finish, invitations arrive, or quotas approach their limits."
              />
            </div>
          ) : (
            <ul className="divide-y">
              {list.map((n) => (
                <li
                  key={n.id}
                  className={
                    n.readAt
                      ? "p-4"
                      : "bg-primary/5 border-l-primary border-l-4 p-4"
                  }
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs">
                          {n.kind.replace(/_/g, " ")}
                        </Badge>
                        <span className="text-muted-foreground text-xs">
                          {new Date(n.createdAt).toLocaleString()}
                        </span>
                      </div>
                      <p className="mt-1 text-sm font-medium">{n.title}</p>
                      {n.body && (
                        <p className="text-muted-foreground mt-0.5 text-sm">
                          {n.body}
                        </p>
                      )}
                    </div>
                    {!n.readAt && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          markRead({ variables: { input: { id: n.id } } })
                        }
                        disabled={marking}
                      >
                        Mark read
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
