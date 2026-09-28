"use client";

import { ActivityIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { PayloadPreview, SourceBadge } from "./EventSourceBadge";
import type { useRawEvents } from "./use-events";

export type RawEventsListProps = ReturnType<typeof useRawEvents> & {
  toolbar: React.ReactNode;
};

/** The ungrouped stream: one row per event, each a link to its detail. */
export function RawEventsList({ table, toolbar }: RawEventsListProps) {
  const t = useTranslations("lists.events");
  const fmt = useFormatters();

  // No sort controls: `astroliftEventsPage` takes no sort argument (it
  // seeks on `occurred_at, guid`, newest first), and reordering the page in
  // hand while the rest of the stream sits on the server is wrong at every
  // page boundary.
  const columns: Column<AstroliftEvent>[] = [
    {
      id: "type",
      header: "Type",
      cell: (e) => (
        <Badge variant="outline" className="font-mono text-xs">
          {e.eventType}
        </Badge>
      ),
    },
    {
      id: "source",
      header: t("sourceLabel"),
      // The row is a stretched link (`rowHref`) whose ::after covers every
      // cell; the deep-link badge has to sit above it to stay clickable.
      cellClassName: "relative z-10",
      cell: (e) => (
        <SourceBadge
          resourceKind={e.resourceKind ?? ""}
          resourceId={e.resourceId ?? ""}
          payload={e.payload}
        />
      ),
    },
    {
      id: "payload",
      header: "Payload",
      cell: (e) => <PayloadPreview payload={e.payload} />,
    },
    {
      id: "time",
      header: "Time",
      width: "w-52",
      cellClassName: "text-muted-foreground text-xs",
      cell: (e) => fmt.formatDateTime(e.occurredAt),
    },
  ];

  return (
    <DataTable
      label="Events"
      controller={table}
      columns={columns}
      getRowId={(e) => e.id}
      rowHref={(e) => `/events/${e.id}`}
      toolbar={toolbar}
      searchPlaceholder={t("filterPlaceholder")}
      empty={{
        icon: <ActivityIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
      }}
      emptyFiltered={{
        title: "No matching events",
        description:
          "No event matches that search. It looks at the event type, the resource kind and id, and the app slug — try a shorter term, or clear the search to see the whole stream.",
      }}
    />
  );
}
