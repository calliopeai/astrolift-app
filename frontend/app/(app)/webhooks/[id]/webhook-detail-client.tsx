"use client";

import { useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_WEBHOOK_DELIVERIES, LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import type {
  AstroliftWebhookDelivery,
  AstroliftWebhookSubscription,
} from "@/graphql/operations/operations.types";

interface Resp {
  astroliftWebhookSubscriptions: AstroliftWebhookSubscription[];
}
interface DeliveriesResp {
  astroliftWebhookDeliveries: AstroliftWebhookDelivery[];
}

const DELIVERIES_LIMIT = 50;

const ROW_NAV_CLASS =
  "hover:bg-accent/30 focus-visible:outline-ring cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-[-2px]";

/**
 * Webhook subscription detail (#1106). Reuses the global LIST_WEBHOOKS window
 * (no singular query exists) plus LIST_WEBHOOK_DELIVERIES for the recent
 * deliveries table, whose rows drill into the per-delivery detail.
 */
export function WebhookDetailClient({ id }: { id: string }) {
  const router = useRouter();
  const { data, loading } = useQuery<Resp>(LIST_WEBHOOKS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const s = React.useMemo(
    () => (data?.astroliftWebhookSubscriptions ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  const deliveries = useQuery<DeliveriesResp>(LIST_WEBHOOK_DELIVERIES, {
    variables: { subscriptionId: id, limit: DELIVERIES_LIMIT },
    fetchPolicy: "cache-and-network",
    skip: !s,
  });
  const deliveryRows = deliveries.data?.astroliftWebhookDeliveries ?? [];

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
          <CardContent className="p-0">
            {deliveries.loading && deliveryRows.length === 0 ? (
              <div className="p-6">
                <Skeleton className="h-16 w-full" />
              </div>
            ) : deliveryRows.length === 0 ? (
              <p className="text-muted-foreground p-6 text-sm">
                No deliveries yet. Use the Send test event action on the webhooks list to fire one.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-12">#</TableHead>
                    <TableHead>When</TableHead>
                    <TableHead>Event</TableHead>
                    <TableHead className="w-16">Status</TableHead>
                    <TableHead className="w-20">Latency</TableHead>
                    <TableHead className="w-16">Test?</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {deliveryRows.map((d) => (
                    <TableRow
                      key={d.id}
                      tabIndex={0}
                      role="link"
                      aria-label={`Open delivery ${d.id.slice(0, 8)}`}
                      onClick={() => router.push(`/webhooks/${id}/deliveries/${d.id}`)}
                      onKeyDown={(ev) => {
                        if (ev.key === "Enter" || ev.key === " ") {
                          ev.preventDefault();
                          router.push(`/webhooks/${id}/deliveries/${d.id}`);
                        }
                      }}
                      className={ROW_NAV_CLASS}
                    >
                      <TableCell className="font-mono text-xs">{d.retryAttempt}</TableCell>
                      <TableCell className="text-xs">
                        <DetailTimestamp iso={d.deliveredAt} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{d.eventType}</TableCell>
                      <TableCell>
                        <Badge variant={d.success ? "secondary" : "outline"} className="text-2xs">
                          {d.statusCode ?? "ERR"}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{d.latencyMs}ms</TableCell>
                      <TableCell>
                        {d.isTest && (
                          <Badge variant="outline" className="text-2xs">
                            test
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
