"use client";

import { ActivityIcon, ChevronRightIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { DataTable, type Column } from "@/components/data-table";
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
import type { AggregatedEvent, useAggregatedEvents } from "./use-events";

export type AggregatedEventsListProps = ReturnType<typeof useAggregatedEvents> & {
  toolbar: React.ReactNode;
  /**
   * The open bucket's members. A slot, so the members fetch only runs
   * while the dialog is open.
   */
  renderMembers: (bucket: AggregatedEvent) => React.ReactNode;
};

/** The grouped stream: one row per bucket of repeats, opening its members. */
export function AggregatedEventsList({ table, toolbar, renderMembers }: AggregatedEventsListProps) {
  const t = useTranslations("lists.events");
  const fmt = useFormatters();
  const [openBucket, setOpenBucket] = React.useState<AggregatedEvent | null>(null);

  const columns: Column<AggregatedEvent>[] = [
    {
      id: "type",
      header: "Type",
      cell: (b) => (
        <span className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono text-xs">
            {b.eventType}
          </Badge>
          {b.count > 1 && (
            <Badge variant="secondary" className="text-xs">
              {t("aggregateBadge", { count: b.count })}
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "source",
      header: t("sourceLabel"),
      cell: (b) => (
        <SourceBadge
          resourceKind={b.resourceKind}
          resourceId={b.resourceId}
          payload={b.representative.payload}
        />
      ),
    },
    {
      id: "window",
      header: "Window",
      cellClassName: "text-muted-foreground text-xs",
      cell: (b) =>
        b.count > 1
          ? `${fmt.formatDateTime(b.firstAt)} → ${fmt.formatDateTime(b.lastAt)}`
          : fmt.formatDateTime(b.lastAt),
    },
    {
      id: "members",
      header: <span className="sr-only">Members</span>,
      align: "right",
      width: "w-32",
      // The row opens the same dialog, but a `<tr>` onClick is not
      // keyboard-reachable — this button is what makes the bucket's
      // members openable without a mouse. Its visible text is its
      // accessible name; the member count is already in the type cell.
      cell: (b) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={(ev) => {
            ev.stopPropagation();
            setOpenBucket(b);
          }}
        >
          {t("expandLabel")}
          <ChevronRightIcon className="size-3.5" />
        </Button>
      ),
    },
  ];

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
      <DataTable
        label="Event groups"
        controller={table}
        columns={columns}
        getRowId={(b) => `${b.eventType}|${b.resourceKind}|${b.resourceId}|${b.representative.id}`}
        // Buckets are folds, not rows, so they have no URL of their own to
        // link to: activating one opens its members instead of navigating.
        onRowActivate={(b) => setOpenBucket(b)}
        // The bucket's own identity, which is what the first cell shows:
        // the event type, plus the fold size when it folded anything.
        rowLabel={(b) => (b.count > 1 ? `${b.eventType} (${b.count})` : b.eventType)}
        toolbar={toolbar}
        searchPlaceholder={t("filterPlaceholder")}
        empty={{
          icon: <ActivityIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
        emptyFiltered={{
          title: "No matching event groups",
          description:
            "No group matches that search. It looks at the event type, the resource kind and id, and the app slug — try a shorter term, or clear the search to see the whole stream.",
        }}
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
