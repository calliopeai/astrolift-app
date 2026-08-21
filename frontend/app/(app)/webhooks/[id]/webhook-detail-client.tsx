"use client";

import { useQuery } from "@apollo/client/react";
import { SendIcon } from "lucide-react";
import * as React from "react";

import { DataTable, useCursorTable, type Column, type CursorPage } from "@/components/data-table";
import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  LIST_WEBHOOK_DELIVERIES_PAGE,
  LIST_WEBHOOKS,
} from "@/graphql/operations/operations.queries";
import type {
  AstroliftWebhookDelivery,
  AstroliftWebhookSubscription,
} from "@/graphql/operations/operations.types";

interface Resp {
  astroliftWebhookSubscriptions: AstroliftWebhookSubscription[];
}
interface DeliveriesPageResp {
  astroliftWebhookDeliveriesPage: CursorPage<AstroliftWebhookDelivery>;
}

/**
 * Webhook subscription detail (#1106). Reuses the global LIST_WEBHOOKS window
 * (no singular query exists) plus LIST_WEBHOOK_DELIVERIES_PAGE for the recent
 * deliveries table, whose rows drill into the per-delivery detail.
 */
export function WebhookDetailClient({ id }: { id: string }) {
  const { data, loading } = useQuery<Resp>(LIST_WEBHOOKS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const s = React.useMemo(
    () => (data?.astroliftWebhookSubscriptions ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  // Skipped until the subscription resolves: `subscriptionId` is
  // non-null on the field, and this page reaches it through the global
  // LIST_WEBHOOKS window rather than a singular query. No `urlKey` —
  // the search and page size belong to this panel, not to the URL an
  // operator shares for the subscription.
  const deliveries = useCursorTable<AstroliftWebhookDelivery>({
    query: LIST_WEBHOOK_DELIVERIES_PAGE,
    variables: { subscriptionId: id },
    extract: (d) => (d as DeliveriesPageResp | undefined)?.astroliftWebhookDeliveriesPage,
    searchVariable: "search",
    fetchPolicy: "cache-and-network",
    skip: !s,
  });

  const columns: Column<AstroliftWebhookDelivery>[] = [
    {
      id: "attempt",
      header: "#",
      width: "w-12",
      cellClassName: "font-mono text-xs",
      cell: (d) => d.retryAttempt,
    },
    {
      id: "when",
      header: "When",
      cellClassName: "text-xs",
      cell: (d) => <DetailTimestamp iso={d.deliveredAt} />,
    },
    {
      id: "event",
      header: "Event",
      cellClassName: "font-mono text-xs",
      cell: (d) => d.eventType,
    },
    {
      id: "status",
      header: "Status",
      width: "w-16",
      cell: (d) => (
        <Badge variant={d.success ? "secondary" : "outline"} className="text-2xs">
          {d.statusCode ?? "ERR"}
        </Badge>
      ),
    },
    {
      id: "latency",
      header: "Latency",
      width: "w-20",
      cellClassName: "font-mono text-xs",
      cell: (d) => `${d.latencyMs}ms`,
    },
    {
      id: "test",
      header: "Test?",
      width: "w-16",
      cell: (d) =>
        d.isTest ? (
          <Badge variant="outline" className="text-2xs">
            test
          </Badge>
        ) : null,
    },
  ];

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!s}
      breadcrumb={{ label: "Webhooks", href: "/webhooks" }}
      heading={s ? s.url : `Webhook ${id.slice(0, 8)}`}
      status={s ? (s.isActive ? "active" : "paused") : undefined}
      statusTone={s ? (s.isActive ? "ok" : "muted") : undefined}
      createdAt={s?.createdAt}
      notFoundLabel="webhook"
      overview={
        s
          ? [
              { term: "URL", description: <span className="font-mono text-xs break-all">{s.url}</span> },
              {
                term: "Events",
                description: (
                  <span className="flex flex-wrap gap-1">
                    {s.events.length === 0 ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      s.events.map((ev) => (
                        <Badge key={ev} variant="outline" className="text-xs">
                          {ev}
                        </Badge>
                      ))
                    )}
                  </span>
                ),
              },
              { term: "Format", description: <span className="capitalize">{s.format}</span> },
              {
                term: "Failures",
                description:
                  s.failureCount > 0 ? (
                    <span className="text-destructive">{s.failureCount}</span>
                  ) : (
                    "0"
                  ),
              },
              {
                term: "Last delivery",
                description: s.lastDeliveryAt ? (
                  <span>
                    <DetailTimestamp iso={s.lastDeliveryAt} />
                    {s.lastResponseStatus != null ? (
                      <span className="text-muted-foreground"> (HTTP {s.lastResponseStatus})</span>
                    ) : null}
                  </span>
                ) : (
                  <span className="text-muted-foreground">never</span>
                ),
              },
              { term: "Secret rotated", description: <DetailTimestamp iso={s.secretRotatedAt} /> },
              { term: "Version", description: <span className="font-mono text-xs">{s.version}</span> },
              { term: "Created", description: <DetailTimestamp iso={s.createdAt} /> },
            ]
          : []
      }
    >
      {s ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent deliveries</CardTitle>
          </CardHeader>
          <CardContent>
            <DataTable
              label="Deliveries"
              controller={deliveries}
              columns={columns}
              getRowId={(d) => d.id}
              // A real link, so a delivery can be opened in a new tab. The
              // hand-rolled row was `role="link"` on a <tr> with a
              // `router.push`, which middle-click and copy-link could not
              // reach.
              rowHref={(d) => `/webhooks/${id}/deliveries/${d.id}`}
              searchPlaceholder="Search deliveries…"
              empty={{
                icon: <SendIcon className="size-5" />,
                title: "No deliveries yet",
                description:
                  "Use the Send test event action on the webhooks list to fire one.",
              }}
              emptyFiltered={{
                title: "No matching deliveries",
                description:
                  "No attempt matches that search. The server matches the event type, the delivery id, and the error text.",
              }}
            />
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
