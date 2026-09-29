"use client";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { DeliveriesFeed } from "./DeliveriesFeed";
import type { WebhookDetailData } from "./use-webhook-detail";

/**
 * Webhook subscription detail (#1106): the subscription's overview plus
 * its delivery log as a Feed (list rule 5: deliveries keep arriving), whose
 * lines drill into the per-delivery detail.
 */
export function WebhookDetailScreen({
  id,
  loading,
  subscription: s,
  deliveries,
}: WebhookDetailData) {
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
              {
                term: "URL",
                description: <span className="font-mono text-xs break-all">{s.url}</span>,
              },
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
              {
                term: "Version",
                description: <span className="font-mono text-xs">{s.version}</span>,
              },
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
            <DeliveriesFeed
              deliveries={deliveries}
              hrefOf={(d) => `/webhooks/${id}/deliveries/${d.id}`}
              emptyHint="Use the Send test event action on the webhooks list to fire one."
            />
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
