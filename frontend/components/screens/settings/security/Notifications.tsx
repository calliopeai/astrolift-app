"use client";

import { BellIcon, BellOffIcon, CheckCheckIcon } from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useNotifications } from "./use-notifications";

export type NotificationsScreenProps = ReturnType<typeof useNotifications> & {
  /** The alert-subscriptions matrix, rendered above the inbox. */
  alertSubscriptions: React.ReactNode;
};

/** Settings > Notifications: alert subscriptions plus the caller's inbox. */
export function NotificationsScreen({
  notifications: list,
  loading,
  marking,
  markingAll,
  onMarkRead,
  onMarkAll,
  alertSubscriptions,
}: NotificationsScreenProps) {
  const fmt = useFormatters();
  const unread = list.filter((n) => !n.readAt);

  return (
    <PageShell
      title="Notifications"
      description="Your inbox: deploy approvals, failure alerts, invitations, quota warnings."
    >
      {alertSubscriptions}

      <div className="flex items-center justify-between gap-3">
        <Badge variant="outline" className="gap-1">
          <BellIcon className="size-3" />
          {unread.length} unread
        </Badge>
        {unread.length > 0 && (
          <Button size="sm" variant="outline" onClick={onMarkAll} disabled={markingAll}>
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
                  className={n.readAt ? "p-4" : "bg-primary/5 border-l-primary border-l-4 p-4"}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs">
                          {n.kind.replace(/_/g, " ")}
                        </Badge>
                        <span className="text-muted-foreground text-xs">
                          {fmt.formatDateTime(n.createdAt)}
                        </span>
                      </div>
                      <p className="mt-1 text-sm font-medium">{n.title}</p>
                      {n.body && <p className="text-muted-foreground mt-0.5 text-sm">{n.body}</p>}
                    </div>
                    {!n.readAt && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onMarkRead(n.id)}
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
