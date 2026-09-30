"use client";

import { BellIcon, BellOffIcon, CheckCheckIcon } from "lucide-react";

import { Feed } from "@/components/feed/Feed";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { AstroliftNotification } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useNotifications } from "./use-notifications";

export type NotificationsInboxProps = Omit<ReturnType<typeof useNotifications>, "more"> & {
  /** Load older (useGrowingLimit); absent, the feed ends. */
  more?: ReturnType<typeof useNotifications>["more"];
};

/**
 * Settings › Notifications › Inbox: the caller's notifications as a Feed in
 * its own frame (list rule 5), newest first and grouped by day, unread ones
 * marked. `astroliftMyNotifications` takes a limit and no cursor, so older
 * notifications load by asking for a larger window as the reader nears the
 * end (useGrowingLimit) until the field pages.
 * Pure; the data half is useNotifications.
 */
export function NotificationsInbox({
  notifications,
  loading,
  error,
  onRetry,
  marking,
  markingAll,
  onMarkRead,
  onMarkAll,
  more,
}: NotificationsInboxProps) {
  const fmt = useFormatters();
  const unread = notifications.filter((n) => !n.readAt);

  return (
    <Section
      title="Inbox"
      description="Deploy approvals, failure alerts, invitations, quota warnings."
    >
      <div className="flex min-w-0 items-center justify-between gap-3">
        <Badge variant="outline" className="gap-1">
          <BellIcon className="size-3" />
          <span className="font-mono">{unread.length}</span> unread
        </Badge>
        {unread.length > 0 && (
          <Button size="sm" variant="outline" onClick={onMarkAll} disabled={markingAll}>
            <CheckCheckIcon className="size-4" />
            Mark all as read
          </Button>
        )}
      </div>

      <Feed<AstroliftNotification>
        label="Notifications"
        items={notifications}
        loading={loading && notifications.length === 0}
        error={error}
        onRetry={onRetry}
        {...more}
        keyOf={(n) => n.id}
        groupBy={{ day: (n) => n.createdAt }}
        maxHeight="max-h-128"
        empty={{
          icon: <BellOffIcon className="size-5" />,
          title: "Inbox empty",
          description:
            "Notifications appear here as deploys finish, invitations arrive, or quotas approach their limits.",
        }}
        renderItem={(n) => (
          <div
            className={
              n.readAt
                ? "flex min-w-0 items-start justify-between gap-3 px-2"
                : "bg-primary/5 border-l-primary flex min-w-0 items-start justify-between gap-3 border-l-4 px-2"
            }
          >
            <div className="min-w-0">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Badge variant="outline" className="text-xs">
                  {n.kind.replace(/_/g, " ")}
                </Badge>
                <time dateTime={n.createdAt} className="text-muted-foreground font-mono text-xs">
                  {fmt.formatDateTime(n.createdAt)}
                </time>
              </div>
              <p className="mt-1 text-sm font-medium [overflow-wrap:anywhere]">{n.title}</p>
              {n.body && (
                <p className="text-muted-foreground mt-0.5 text-sm [overflow-wrap:anywhere]">
                  {n.body}
                </p>
              )}
            </div>
            {!n.readAt && (
              <Button size="sm" variant="ghost" onClick={() => onMarkRead(n.id)} disabled={marking}>
                Mark read
              </Button>
            )}
          </div>
        )}
      />
    </Section>
  );
}
