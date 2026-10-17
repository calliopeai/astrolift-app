"use client";

import {
  ActivityIcon,
  BotIcon,
  CircleDollarSignIcon,
  NetworkIcon,
  ServerIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";

import { QueryError } from "@/components/QueryError";
import { StatusDot } from "@/components/StatusDot";
import { Card, CardContent } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";

import {
  gatewayHealth,
  HEALTH_DOT,
  isDegraded,
  liveClusters,
  type ReadState,
  type UsageTotals,
  type ZentinelleConnection,
} from "./zentinelle-model";

export interface ZentinelleHealth {
  /** Usage over the last 24 hours and this month; null without usage_view. */
  usage: ReadState<{ day: UsageTotals; month: UsageTotals } | null>;
}

export interface ZentinelleOverviewProps extends ZentinelleHealth {
  /** Null when the viewer cannot read the connection (no status_view). */
  connection: ZentinelleConnection | null;
}

/**
 * The health card: agents observed, requests in the last 24 hours, spend
 * this month, and each registered cluster's gateway.
 */
export function ZentinelleOverview({ connection, usage }: ZentinelleOverviewProps) {
  const t = useTranslations("zentinelleScreen.overview");
  const th = useTranslations("zentinelleScreen.health");
  const format = useFormatter();
  const clusters = liveClusters(connection);
  const installEnabled = connection?.gatewayFeatureEnabled ?? false;
  const healthy = clusters.filter((c) => gatewayHealth(c, installEnabled) === "healthy").length;
  const day = usage.data?.day;
  const month = usage.data?.month;

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {isDegraded(connection) && (
        <div
          role="status"
          className="bg-warning-bg text-warning-fg border-warning-border flex items-start gap-2 rounded-md border p-3 text-sm"
        >
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
          {t("degraded")}
        </div>
      )}
      <Section title={t("title")}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {connection && (
            <StatTile
              label={t("gateways")}
              value={t("gatewaysValue", { healthy, total: clusters.length })}
              icon={NetworkIcon}
            />
          )}
          {usage.data !== null || usage.loading ? (
            <>
              <StatTile
                label={t("agents")}
                value={month?.agents}
                icon={BotIcon}
                loading={usage.loading}
              />
              <StatTile
                label={t("requests")}
                value={day ? format.number(day.requests) : null}
                icon={ActivityIcon}
                loading={usage.loading}
              />
              <StatTile
                label={t("spend")}
                value={
                  month
                    ? format.number(month.costUsd, { style: "currency", currency: "USD" })
                    : null
                }
                icon={CircleDollarSignIcon}
                loading={usage.loading}
              />
            </>
          ) : null}
        </div>
        {usage.error && (
          <div className="mt-4">
            <QueryError title={t("usageError")} error={usage.error} onRetry={usage.onRetry} />
          </div>
        )}
      </Section>

      {connection && (
        <Section title={t("clustersTitle")} description={t("clustersDescription")}>
          <Card>
            <CardContent className="flex min-w-0 flex-col">
              {!installEnabled && (
                <p className="text-muted-foreground border-b py-3 text-sm">{t("installOff")}</p>
              )}
              {clusters.length === 0 ? (
                <p className="text-muted-foreground py-6 text-center text-sm">{t("noClusters")}</p>
              ) : (
                clusters.map((cluster) => {
                  const health = gatewayHealth(cluster, installEnabled);
                  return (
                    <div
                      key={cluster.clusterId}
                      className="border-border/60 flex min-w-0 flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
                    >
                      <ServerIcon className="text-muted-foreground size-4 shrink-0" />
                      <span className="min-w-0 flex-1 font-mono text-sm [overflow-wrap:anywhere]">
                        {cluster.clusterSlug}
                      </span>
                      <span className="inline-flex items-center gap-2 text-sm">
                        <StatusDot status={HEALTH_DOT[health]} />
                        {th(health)}
                      </span>
                      {cluster.lastError && (
                        <p className="text-danger-fg w-full text-xs [overflow-wrap:anywhere]">
                          {cluster.lastError}
                        </p>
                      )}
                    </div>
                  );
                })
              )}
            </CardContent>
          </Card>
        </Section>
      )}
    </div>
  );
}
