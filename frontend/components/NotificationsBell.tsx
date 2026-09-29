"use client";

import { BellIcon, BellOffIcon, CheckCheckIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { AstroliftNotification } from "@/graphql/operations/operations.types";

/**
 * The notifications bell and its popover. Pure (Storybook first): the list
 * and the read actions come from useMyNotifications, wired by the shell.
 */
export interface NotificationsBellProps {
  notifications: AstroliftNotification[];
  loading: boolean;
  markingAll: boolean;
  onMarkRead: (id: string) => void;
  onMarkAll: () => void;
}

export function NotificationsBell({
  notifications: list,
  loading,
  markingAll,
  onMarkRead,
  onMarkAll,
}: NotificationsBellProps) {
  const unread = list.filter((n) => !n.readAt);
  const recent = list.slice(0, 5);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label="Notifications">
          <BellIcon className="size-5" />
          {unread.length > 0 && (
            <Badge
              variant="destructive"
              className="text-2xs absolute -top-1 -right-1 h-4 min-w-[1rem] rounded-full px-1 leading-none tabular-nums"
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
                onClick={onMarkAll}
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
          <div className="text-muted-foreground px-4 py-8 text-center text-sm">Loading…</div>
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
                        onMarkRead(n.id);
                      }
                    }}
                    className="block"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="outline" className="text-2xs">
                        {n.kind.replace(/_/g, " ")}
                      </Badge>
                      <span className="text-muted-foreground text-2xs">{timeAgo(n.createdAt)}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 font-medium">{n.title}</p>
                    {n.body && (
                      <p className="text-muted-foreground line-clamp-2 text-xs">{n.body}</p>
                    )}
                  </Wrapper>
                </li>
              );
            })}
          </ul>
        )}

        <div className="border-t">
          <Button asChild variant="ghost" size="sm" className="w-full justify-center text-xs">
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
