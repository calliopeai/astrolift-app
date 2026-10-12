"use client";

import { useTranslations } from "next-intl";
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
  const t = useTranslations("webhooksScreen");
  return (
    <EntityDetailShell
      loading={loading}
      notFound={!d}
      breadcrumb={{ label: t("webhook"), href: `/webhooks/${subscriptionId}` }}
      heading={t("deliveryHeading", { id: deliveryId.slice(0, 8) })}
      status={d ? (d.success ? t("delivered") : t("failed")) : undefined}
      statusTone={d ? (d.success ? "ok" : "error") : undefined}
      createdAt={d?.deliveredAt}
      notFoundLabel={t("deliveryNoun")}
      overview={
        d
          ? [
              {
                term: t("event"),
                description: <span className="font-mono text-xs">{d.eventType}</span>,
              },
              {
                term: t("httpStatus"),
                description: (
                  <Badge variant={d.success ? "secondary" : "outline"}>
                    {d.statusCode ?? "ERR"}
                  </Badge>
                ),
              },
              {
                term: t("attempt"),
                description: <span className="font-mono text-xs">{d.retryAttempt}</span>,
              },
              { term: t("latency"), description: t("ms", { ms: d.latencyMs }) },
              { term: t("test"), description: d.isTest ? t("yes") : t("no") },
              {
                term: t("deliveryId"),
                description: (
                  <span className="font-mono text-xs break-all">{d.deliveryId || "—"}</span>
                ),
              },
              { term: t("deliveredAt"), description: <DetailTimestamp iso={d.deliveredAt} /> },
            ]
          : []
      }
    >
      {d ? (
        <>
          {d.error ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("error")}</CardTitle>
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
              <CardTitle className="text-base">{t("requestPayload")}</CardTitle>
            </CardHeader>
            <CardContent>
              {d.requestPayloadExcerpt ? (
                <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                  {d.requestPayloadExcerpt}
                </pre>
              ) : (
                <p className="text-muted-foreground text-sm">{t("noRequestPayload")}</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("responseBody")}</CardTitle>
            </CardHeader>
            <CardContent>
              {d.responseBodyExcerpt ? (
                <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                  {d.responseBodyExcerpt}
                </pre>
              ) : (
                <p className="text-muted-foreground text-sm">{t("noResponseBody")}</p>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </EntityDetailShell>
  );
}
