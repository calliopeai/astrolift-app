"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { BellIcon, BellOffIcon, CheckCheckIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
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

export function NotificationsBell() {
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

  const list = data?.astroliftMyNotifications ?? [];
  const unread = list.filter((n) => !n.readAt);
  const recent = list.slice(0, 5);

  async function handleMarkAll() {
    const { data } = await markAll();
    if (data?.markAllNotificationsRead.ok) {
      toast.success(`Cleared ${data.markAllNotificationsRead.data?.marked ?? 0}`);
    }
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label="Notifications"
        >
          <BellIcon className="size-5" />
          {unread.length > 0 && (
            <Badge
              variant="destructive"
              className="absolute -right-1 -top-1 h-4 min-w-[1rem] rounded-full px-1 text-2xs tabular-nums leading-none"
            >
              {unread.length > 99 ? "99+" : unread.length}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <p className="text-sm font-medium">Notifications</p>
          <div className="flex items-center gap-1">
            {unread.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                onClick={handleMarkAll}
                disabled={markingAll}
                className="h-7 text-xs"
              >
                <CheckCheckIcon className="size-3" />
                Mark all
              </Button>
            )}
          </div>
        </div>

        {loading && list.length === 0 ? (
          <div className="text-muted-foreground px-4 py-8 text-center text-sm">
            Loading…
          </div>
        ) : recent.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
            <BellOffIcon className="text-muted-foreground size-5" />
            <p className="text-muted-foreground text-sm">All clear.</p>
          </div>
        ) : (
          <ul className="max-h-96 divide-y overflow-y-auto">
            {recent.map((n) => {
              const Wrapper: React.ElementType = n.link ? Link : "div";
              const wrapperProps = n.link
                ? { href: n.link, prefetch: false }
                : ({} as Record<string, unknown>);
              return (
                <li
                  key={n.id}
                  className={
                    n.readAt
                      ? "p-3 text-sm"
                      : "bg-primary/5 border-l-primary border-l-2 p-3 text-sm"
                  }
                >
                  <Wrapper
                    {...(wrapperProps as Record<string, unknown>)}
                    onClick={() => {
                      if (!n.readAt) {
                        markRead({ variables: { input: { id: n.id } } });
                      }
                    }}
                    className="block"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="outline" className="text-2xs">
                        {n.kind.replace(/_/g, " ")}
                      </Badge>
                      <span className="text-muted-foreground text-2xs">
                        {timeAgo(n.createdAt)}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 font-medium">{n.title}</p>
                    {n.body && (
                      <p className="text-muted-foreground line-clamp-2 text-xs">
                        {n.body}
                      </p>
                    )}
                  </Wrapper>
                </li>
              );
            })}
          </ul>
        )}

        <div className="border-t">
          <Button
            asChild
            variant="ghost"
            size="sm"
            className="w-full justify-center text-xs"
          >
            <Link href="/settings/notifications">View all</Link>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function timeAgo(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}
