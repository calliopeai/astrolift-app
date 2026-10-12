"use client";

import { useTranslations } from "next-intl";

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
  const t = useTranslations("webhooksScreen");
  return (
    <EntityDetailShell
      loading={loading}
      notFound={!s}
      breadcrumb={{ label: t("title"), href: "/webhooks" }}
      heading={s ? s.url : t("webhookHeading", { id: id.slice(0, 8) })}
      status={s ? (s.isActive ? t("active") : t("paused")) : undefined}
      statusTone={s ? (s.isActive ? "ok" : "muted") : undefined}
      createdAt={s?.createdAt}
      notFoundLabel={t("webhookNoun")}
      overview={
        s
          ? [
              {
                term: t("columns.url"),
                description: <span className="font-mono text-xs break-all">{s.url}</span>,
              },
              {
                term: t("columns.events"),
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
              {
                term: t("columns.format"),
                description: <span className="capitalize">{s.format}</span>,
              },
              {
                term: t("columns.failures"),
                description:
                  s.failureCount > 0 ? (
                    <span className="text-destructive">{s.failureCount}</span>
                  ) : (
                    "0"
                  ),
              },
              {
                term: t("columns.lastDelivery"),
                description: s.lastDeliveryAt ? (
                  <span>
                    <DetailTimestamp iso={s.lastDeliveryAt} />
                    {s.lastResponseStatus != null ? (
                      <span className="text-muted-foreground">
                        {" "}
                        {t("httpParen", { status: s.lastResponseStatus })}
                      </span>
                    ) : null}
                  </span>
                ) : (
                  <span className="text-muted-foreground">{t("never")}</span>
                ),
              },
              {
                term: t("secretRotated"),
                description: <DetailTimestamp iso={s.secretRotatedAt} />,
              },
              {
                term: t("version"),
                description: <span className="font-mono text-xs">{s.version}</span>,
              },
              { term: t("created"), description: <DetailTimestamp iso={s.createdAt} /> },
            ]
          : []
      }
    >
      {s ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("recentDeliveries")}</CardTitle>
          </CardHeader>
          <CardContent>
            <DeliveriesFeed
              deliveries={deliveries}
              hrefOf={(d) => `/webhooks/${id}/deliveries/${d.id}`}
              emptyHint={t("emptyHintList")}
            />
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
