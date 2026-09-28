"use client";

import { SendIcon } from "lucide-react";
import * as React from "react";

import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import type {
  AstroliftWebhookDelivery,
  AstroliftWebhookSubscription,
} from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { SubscriptionDeliveriesData } from "./use-webhooks";

export type SubscriptionDetailViewProps = SubscriptionDeliveriesData & {
  subscription: AstroliftWebhookSubscription;
  onClose: () => void;
};

/**
 * Subscription detail, rendered as a panel under the table rather than as
 * an extra `<tr>` inside it: DataTable owns one row per item, and a
 * detail row folded into the body would have to re-derive the table
 * primitives this surface just stopped importing.
 */
export function SubscriptionDetailView({
  subscription,
  onClose,
  deliveries,
}: SubscriptionDetailViewProps) {
  const fmt = useFormatters();

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
      cell: (d) => fmt.formatDateTime(d.deliveredAt),
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
    {
      id: "response",
      header: "Response",
      cellClassName: "max-w-xs truncate font-mono text-xs",
      cell: (d) => d.error || d.responseBodyExcerpt || "—",
    },
  ];

  return (
    <Card className="bg-muted/30">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="font-mono text-xs break-all">{subscription.url}</p>
          <Button size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>

        <DefinitionList
          orientation="stack"
          className="grid grid-cols-2 gap-3 md:grid-cols-4"
          items={[
            {
              term: "Secret rotated",
              description: subscription.secretRotatedAt
                ? fmt.formatDateTime(subscription.secretRotatedAt)
                : "never",
            },
            {
              term: "Last delivery",
              description: subscription.lastDeliveryAt
                ? fmt.formatDateTime(subscription.lastDeliveryAt)
                : "never",
            },
            { term: "Last status", description: subscription.lastResponseStatus ?? "—" },
            { term: "Failure count", description: subscription.failureCount },
          ]}
        />

        <div className="space-y-2">
          <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Deliveries
          </p>
          <DataTable
            label="Deliveries"
            controller={deliveries}
            columns={columns}
            getRowId={(d) => d.id}
            searchPlaceholder="Search deliveries…"
            empty={{
              icon: <SendIcon className="size-5" />,
              title: "No deliveries yet",
              description: "Use the Send test event button to fire one.",
            }}
            emptyFiltered={{
              title: "No matching deliveries",
              description:
                "No attempt matches that search. The server matches the event type, the delivery id, and the error text.",
            }}
          />
        </div>
      </CardContent>
    </Card>
  );
}
