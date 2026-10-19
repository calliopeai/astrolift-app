"use client";

import {
  ActivityIcon,
  BotIcon,
  HandIcon,
  NetworkIcon,
  ServerIcon,
  ShieldBanIcon,
  TagIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import * as React from "react";

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
  type ZentinelleCluster,
  type ZentinelleConnection,
  type ZentinelleHealthCounts,
} from "./zentinelle-model";

export interface ZentinelleHealth {
  /** Usage over the last 24 hours and this month; null without usage_view. */
  usage: ReadState<{ day: UsageTotals; month: UsageTotals } | null>;
  /** Blocks and open approvals from Zentinelle; null without status_view. */
  counts: ReadState<ZentinelleHealthCounts | null>;
}

export interface ZentinelleOverviewProps extends ZentinelleHealth {
  /** Null when the viewer cannot read the connection (no status_view). */
  connection: ZentinelleConnection | null;
}

/**
 * The health card: gateways, Zentinelle's version, blocks in the last 24
 * hours, open approvals, agents observed, requests in the last 24 hours,
 * and each registered cluster's gateway. A count
 * an older Zentinelle cannot give reads "Not available".
 */
export function ZentinelleOverview({ connection, usage, counts }: ZentinelleOverviewProps) {
  const [now] = React.useState(() => Date.now());
  const t = useTranslations("zentinelleScreen.overview");
  const th = useTranslations("zentinelleScreen.health");
  const format = useFormatter();
  const clusters = liveClusters(connection);
  const installEnabled = connection?.gatewayFeatureEnabled ?? false;
  const healthy = clusters.filter(
    (c) => gatewayHealth(c, installEnabled, now) === "healthy"
  ).length;
  const day = usage.data?.day;
  const month = usage.data?.month;
  const reported = counts.data?.available ? counts.data : null;
  const unavailable = counts.data?.available === false;
  const count = (value: number | null | undefined) =>
    unavailable ? t("notAvailable") : value == null ? null : format.number(value);

  function seen(cluster: ZentinelleCluster): string {
    if (cluster.gatewayVersion == null) return t("reportNotAvailable");
    if (!cluster.lastSeenAt) return t("neverReported");
    const version = cluster.gatewayVersion || t("versionUnknown");
    return t("reported", {
      version,
      seen: format.relativeTime(new Date(cluster.lastSeenAt), now),
    });
  }

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
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {connection && (
            <StatTile
              label={t("gateways")}
              value={t("gatewaysValue", { healthy, total: clusters.length })}
              icon={NetworkIcon}
            />
          )}
          {connection && (
            <StatTile
              label={t("serverVersion")}
              value={connection.serverVersion ?? t("notAvailable")}
              icon={TagIcon}
            />
          )}
          {counts.data !== null || counts.loading ? (
            <>
              <StatTile
                label={t("blocks")}
                value={count(reported?.blocks)}
                icon={ShieldBanIcon}
                loading={counts.loading}
              />
              <StatTile
                label={t("approvals")}
                value={count(reported?.openApprovals)}
                icon={HandIcon}
                loading={counts.loading}
              />
            </>
          ) : null}
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
            </>
          ) : null}
        </div>
        {usage.error && (
          <div className="mt-4">
            <QueryError title={t("usageError")} error={usage.error} onRetry={usage.onRetry} />
          </div>
        )}
        {counts.error && (
          <div className="mt-4">
            <QueryError title={t("countsError")} error={counts.error} onRetry={counts.onRetry} />
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
                  const health = gatewayHealth(cluster, installEnabled, now);
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
                      <span className="text-muted-foreground w-full text-xs [overflow-wrap:anywhere] sm:w-auto">
                        {seen(cluster)}
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
