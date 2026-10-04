"use client";
import { useFormatter, useTranslations } from "next-intl";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";
import { Button } from "@/components/ui/button";
import { QueryError } from "@/components/QueryError";
import type { ObservationRead } from "./ModelObservationsPanel";
import type { GetModelSubscriptionMetricsQuery } from "@/graphql/__generated__/operations";
export type SubscriptionUsageData = NonNullable<
  GetModelSubscriptionMetricsQuery["astroliftModelSubscriptionMetrics"]
>;
export type ModelSubscriptionUsagePanelProps = {
  appSlug: string;
  environmentName: string;
  read: ObservationRead<SubscriptionUsageData>;
  onRefresh: () => void;
  onClose: () => void;
};
const measures = {
  requests_per_second: "requests",
  error_requests_per_second: "errors",
  response_bytes_per_second: "responseBytes",
  latency_p95: "latency",
} as const;
export function ModelSubscriptionUsagePanel({
  appSlug,
  environmentName,
  read,
  onRefresh,
  onClose,
}: ModelSubscriptionUsagePanelProps) {
  const t = useTranslations("models.shared.subscriptionUsage"),
    observation = useTranslations("models.shared.observations"),
    format = useFormatter();
  const metrics = Object.keys(measures).map((key) =>
    read.data?.metrics.find((metric) => metric.key === key)
  );
  return (
    <Section title={`${t("title")} · ${appSlug} / ${environmentName}`} description={t("scope")}>
      <div className="space-y-3">
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={onRefresh} disabled={read.loading}>
            {observation("refresh")}
          </Button>
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("dismiss")}
          </Button>
        </div>
        {read.loading && <p role="status">{observation("loading")}</p>}
        {read.error &&
          (read.error === t("unavailable") ? (
            <p role="alert">{read.error}</p>
          ) : (
            <QueryError title={t("unavailable")} error={read.error} />
          ))}
        {read.stale && !read.loading && <p role="status">{observation("staleRead")}</p>}
        {read.data && (
          <>
            <p className="text-muted-foreground text-xs">
              {observation("window", { start: read.data.start, end: read.data.end })}
            </p>
            {!read.stale &&
              !read.error &&
              metrics.every((metric) => metric?.state === "UNCONFIGURED") && (
                <p role="status">{t("unconfigured")}</p>
              )}
            {!read.stale &&
              !read.error &&
              metrics.every((metric) => metric?.state === "NO_DATA") && (
                <p role="status">{t("empty")}</p>
              )}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {Object.entries(measures).map(([key, label]) => {
                const metric = read.data!.metrics.find((item) => item.key === key);
                const value =
                  metric &&
                  (metric.state === "AVAILABLE" || metric.state === "STALE") &&
                  metric.value != null &&
                  Number.isFinite(metric.value)
                    ? format.number(metric.value, { maximumFractionDigits: 3 })
                    : null;
                return (
                  <StatTile
                    key={key}
                    label={t(label)}
                    value={value}
                    footer={
                      <span>
                        {metric ? observation(`states.${metric.state}`) : observation("unknown")}
                        {metric ? ` · ${metric.unit}` : ""}
                      </span>
                    }
                  />
                );
              })}
            </div>
          </>
        )}
        <p className="text-muted-foreground text-sm">{t("bytes")}</p>
        <p className="text-muted-foreground text-sm">{t("boundary")}</p>
        <p className="text-muted-foreground text-sm">{t("unsupported")}</p>
      </div>
    </Section>
  );
}
