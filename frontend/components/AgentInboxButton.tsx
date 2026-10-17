"use client";

import { InboxIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * The shell's way into the agent inbox (#2434): the count of agent messages
 * waiting for the person. Pure; the shell wires the count.
 */
export interface AgentInboxButtonProps {
  unreadCount: number;
}

export function AgentInboxButton({ unreadCount }: AgentInboxButtonProps) {
  const t = useTranslations("agentInbox");
  const label = unreadCount > 0 ? t("buttonUnread", { count: unreadCount }) : t("title");
  return (
    <Button asChild variant="ghost" size="icon" className="relative">
      <Link href="/inbox" aria-label={label} title={label}>
        <InboxIcon className="size-5" />
        {unreadCount > 0 && (
          <Badge className="text-2xs absolute -top-1 -right-1 h-4 min-w-[1rem] rounded-full px-1 leading-none tabular-nums">
            {unreadCount > 99 ? "99+" : unreadCount}
          </Badge>
        )}
      </Link>
    </Button>
  );
}
