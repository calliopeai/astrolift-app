"use client";

import { ActivityIcon, ChevronRightIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Feed } from "@/components/feed/Feed";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useFormatters } from "@/lib/i18n/formatters";

import { SourceBadge } from "./EventSourceBadge";
import { type AggregatedEvent, bucketKey, type useAggregatedEvents } from "./use-events";

export type AggregatedEventsListProps = ReturnType<typeof useAggregatedEvents> & {
  /**
   * The open bucket's members. A slot, so the members fetch only runs
   * while the dialog is open.
   */
  renderMembers: (bucket: AggregatedEvent) => React.ReactNode;
};

/**
 * The grouped stream: one line per bucket of repeats, opening its members,
 * in a Feed that loads older buckets as the reader nears the end (list
 * rule 5). Pure.
 */
export function AggregatedEventsList({ buckets, renderMembers }: AggregatedEventsListProps) {
  const t = useTranslations("lists.events");
  const fmt = useFormatters();
  const [openBucket, setOpenBucket] = React.useState<AggregatedEvent | null>(null);

  // Two buckets of the same event type differ only by resource, so the
  // dialog has to say which one it opened.
  const bucketSummary = openBucket
    ? [
        openBucket.count > 1
          ? `${openBucket.count} events between ${fmt.formatDateTime(openBucket.firstAt)} and ${fmt.formatDateTime(openBucket.lastAt)}`
          : `1 event at ${fmt.formatDateTime(openBucket.lastAt)}`,
        openBucket.resourceKind || openBucket.resourceId
          ? `${openBucket.resourceKind || "?"}:${openBucket.resourceId || "?"}`
          : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  return (
    <>
      <Feed<AggregatedEvent>
        label="Event groups"
        {...buckets}
        keyOf={bucketKey}
        groupBy={{ day: (b) => b.lastAt }}
        maxHeight="max-h-160"
        empty={{
          icon: <ActivityIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
        renderItem={(b) => (
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-1">
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              <Badge variant="outline" className="max-w-full font-mono text-xs">
                <span className="min-w-0 truncate">{b.eventType}</span>
              </Badge>
              {b.count > 1 && (
                <Badge variant="secondary" className="font-mono text-xs">
                  {t("aggregateBadge", { count: b.count })}
                </Badge>
              )}
            </span>
            <SourceBadge
              resourceKind={b.resourceKind}
              resourceId={b.resourceId}
              payload={b.representative.payload}
            />
            <span className="text-muted-foreground ml-auto flex min-w-0 items-center gap-2 font-mono text-xs">
              <span className="min-w-0 [overflow-wrap:anywhere]">
                {b.count > 1
                  ? `${fmt.formatDateTime(b.firstAt)} → ${fmt.formatDateTime(b.lastAt)}`
                  : fmt.formatDateTime(b.lastAt)}
              </span>
              <Button size="sm" variant="ghost" onClick={() => setOpenBucket(b)}>
                {t("expandLabel")}
                <ChevronRightIcon className="size-3.5" />
              </Button>
            </span>
          </div>
        )}
      />

      <Dialog
        open={openBucket !== null}
        onOpenChange={(next) => {
          if (!next) setOpenBucket(null);
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">{openBucket?.eventType}</DialogTitle>
            <DialogDescription>{bucketSummary}</DialogDescription>
          </DialogHeader>
          {openBucket && renderMembers(openBucket)}
        </DialogContent>
      </Dialog>
    </>
  );
}
