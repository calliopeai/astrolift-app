"use client";

import * as React from "react";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { WebhookDeliveryData } from "./use-webhook-detail";

/**
 * Webhook delivery detail (#1106) — the request/response/timeline behind one
 * delivery attempt. Nested under its subscription because the deliveries query
 * requires a subscriptionId.
 */
export function DeliveryDetailScreen({
  subscriptionId,
  deliveryId,
  loading,
  delivery: d,
}: WebhookDeliveryData) {
  return (
    <EntityDetailShell
      loading={loading}
      notFound={!d}
      breadcrumb={{ label: "Webhook", href: `/webhooks/${subscriptionId}` }}
      heading={`Delivery ${deliveryId.slice(0, 8)}`}
      status={d ? (d.success ? "delivered" : "failed") : undefined}
      statusTone={d ? (d.success ? "ok" : "error") : undefined}
      createdAt={d?.deliveredAt}
      notFoundLabel="delivery"
      overview={
        d
          ? [
              {
                term: "Event",
                description: <span className="font-mono text-xs">{d.eventType}</span>,
              },
              {
                term: "HTTP status",
                description: (
                  <Badge variant={d.success ? "secondary" : "outline"}>
                    {d.statusCode ?? "ERR"}
                  </Badge>
                ),
              },
              {
                term: "Attempt",
                description: <span className="font-mono text-xs">{d.retryAttempt}</span>,
              },
              { term: "Latency", description: `${d.latencyMs}ms` },
              { term: "Test", description: d.isTest ? "Yes" : "No" },
              {
                term: "Delivery ID",
                description: (
                  <span className="font-mono text-xs break-all">{d.deliveryId || "—"}</span>
                ),
              },
              { term: "Delivered", description: <DetailTimestamp iso={d.deliveredAt} /> },
            ]
          : []
      }
    >
      {d ? (
        <>
          {d.error ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Error</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="bg-muted/40 max-h-60 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                  {d.error}
                </pre>
              </CardContent>
            </Card>
          ) : null}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Request payload</CardTitle>
            </CardHeader>
            <CardContent>
              {d.requestPayloadExcerpt ? (
                <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                  {d.requestPayloadExcerpt}
                </pre>
              ) : (
                <p className="text-muted-foreground text-sm">No request payload recorded.</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Response body</CardTitle>
            </CardHeader>
            <CardContent>
              {d.responseBodyExcerpt ? (
                <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                  {d.responseBodyExcerpt}
                </pre>
              ) : (
                <p className="text-muted-foreground text-sm">No response body recorded.</p>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </EntityDetailShell>
  );
}
