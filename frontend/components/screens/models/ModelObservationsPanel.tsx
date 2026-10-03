"use client";
import { useMemo } from "react";
import Link from "next/link";
import { BrainCircuitIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import type {
  GetModelDeploymentMetricsQuery,
  GetClusterModelDensityQuery,
} from "@/graphql/__generated__/operations";
import type { ModelObservationState } from "@/graphql/__generated__/schema";
import { ListPage } from "@/components/list/ListPage";
import { useLocalListState, type ListDefinition } from "@/components/list/use-list-state";
import { QueryError } from "@/components/QueryError";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";
import { DefinitionList } from "@/components/ui/definition-list";
import { Button } from "@/components/ui/button";
import { Sparkline } from "@/components/viz/sparkline";
export type ServingMetrics = NonNullable<
  GetModelDeploymentMetricsQuery["astroliftModelDeploymentMetrics"]
>;
export type ClusterDensity = NonNullable<
  GetClusterModelDensityQuery["astroliftClusterModelDensity"]
>;
export type ModelObservation = ServingMetrics["metrics"][number];
export type ObservationRead<T> = {
  data: T | null;
  loading: boolean;
  stale: boolean;
  error: string | null;
};
export type ModelObservationsPanelProps = {
  metrics: ObservationRead<ServingMetrics>;
  density: ObservationRead<ClusterDensity>;
  clusterId: string;
  clusterName: string;
  organizationId: string;
  onRefresh: () => void;
};
export const MODEL_METRIC_KEYS = [
  "prompt_tokens_per_second",
  "generation_tokens_per_second",
  "successful_requests_per_second",
  "requests_running",
  "requests_waiting",
  "ttft_p95",
  "latency_p95",
  "queue_time_p95",
  "kv_cache_usage",
  "running_replicas",
  "ready_replicas",
  "cpu_usage",
  "memory_usage",
  "cpu_requests",
  "memory_requests",
  "gpu_utilization",
  "vram_usage",
] as const;
export function observedValue(metric: Pick<ModelObservation, "state" | "value">): number | null {
  return (metric.state === "AVAILABLE" || metric.state === "STALE") &&
    metric.value != null &&
    Number.isFinite(metric.value)
    ? metric.value
    : null;
}
export function ModelObservationsPanel({
  metrics,
  density,
  clusterId,
  clusterName,
  organizationId,
  onRefresh,
}: ModelObservationsPanelProps) {
  const inventory = useTranslations("models.shared.inventory");
  const t = useTranslations("models.shared.observations"),
    format = useFormatter();
  const time = (value: string | null | undefined) =>
    value && Number.isFinite(Date.parse(value))
      ? `${format.dateTime(new Date(value), { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC`
      : t("unknown");
  const number = (value: number | null | undefined) =>
    value != null && Number.isFinite(value)
      ? format.number(value, { maximumFractionDigits: 4 })
      : t("unknown");
  const name = (key: string) =>
    (MODEL_METRIC_KEYS as readonly string[]).includes(key) ? t(`metrics.${key}`) : key;
  const state = (value: ModelObservationState) => t(`states.${value}`);
  const catalogueHref = `/models?clusterId=${encodeURIComponent(clusterId)}`;
  const definition = useMemo<ListDefinition>(
    () => ({
      id: "models.shared.density",
      fields: [],
      searchable: false,
      searchPlaceholder: "",
      defaultSort: [],
      views: [{ key: "all", label: t("snapshot"), filters: {} }],
      paging: "cursor",
      pageSizes: [20],
      defaultPageSize: 20,
    }),
    [t]
  );
  const list = useLocalListState(definition);
  const facts = (metric: ModelObservation) => (
    <span className="block space-y-1 break-words">
      <span className="block">
        {state(metric.state)} · {metric.unit}
      </span>
      <span className="block">
        {metric.aggregationWindowSeconds > 0
          ? t("rateWindow", { seconds: metric.aggregationWindowSeconds })
          : t("instant")}
      </span>
      <span className="block">{t("observedAt", { time: time(metric.observedAt) })}</span>
      <span className="block">{t("source", { source: metric.source })}</span>
    </span>
  );
  const requests = (value: ClusterDensity["items"][number]["applied"]) =>
    value ? (
      <div className="space-y-1 text-xs">
        <DefinitionList
          items={[
            { term: t("replicas"), description: number(value.replicas) },
            { term: t("cpu"), description: number(value.totalCpuCores) },
            { term: t("memory"), description: number(value.totalMemoryBytes) },
            {
              term: t("gpu"),
              description: (
                <span>
                  {number(value.totalGpuDevices)}
                  {value.gpuResource && (
                    <code className="block break-all">{value.gpuResource}</code>
                  )}
                </span>
              ),
            },
          ]}
        />
        <p>{t("source", { source: value.source })}</p>
        <p>{t("observedAt", { time: time(value.observedAt) })}</p>
      </div>
    ) : (
      <p>{t("notApplied")}</p>
    );
  const capacity = density.data?.capacity;
  const serving = metrics.data?.metrics.filter((metric) => metric.source === "vllm") ?? [];
  const available =
    metrics.data?.metrics.filter(
      (metric) => metric.state === "AVAILABLE" && observedValue(metric) !== null
    ).length ?? 0;
  const unconfigured =
    serving.length > 0 && serving.every((metric) => metric.state === "UNCONFIGURED");
  const noSamples = serving.length > 0 && serving.every((metric) => metric.state === "NO_DATA");

  const capacityNumber = (value: number | null | undefined) =>
    capacity && (capacity.state === "AVAILABLE" || capacity.state === "STALE")
      ? number(value)
      : t("unknown");
  return (
    <div id="model-metrics" className="scroll-mt-20 space-y-6">
      <Section title={inventory("metrics")} description={inventory("metricsScope")}>
        <div className="mb-4 space-y-2 text-sm">
          <p>{t("scope")}</p>
          {metrics.error ? (
            <p role="status">{inventory("metricsReadUnavailable")}</p>
          ) : (
            !metrics.loading &&
            !metrics.stale && (
              <>
                {available > 0 && <p>{inventory("metricsAvailable", { count: available })}</p>}
                {unconfigured && <p role="status">{inventory("metricsUnconfigured")}</p>}
                {noSamples && <p role="status">{inventory("metricsNoData")}</p>}
              </>
            )
          )}
          <p>{inventory("appTrafficUnavailable")}</p>
          <p>{inventory("costUnavailable")}</p>
          <Button variant="outline" size="sm" asChild>
            <Link href="/documentation/cluster-prerequisites">{inventory("metricsSetup")}</Link>
          </Button>
        </div>
        <div className="space-y-4">
          <Button
            variant="outline"
            onClick={onRefresh}
            disabled={metrics.loading || density.loading}
          >
            {t("refresh")}
          </Button>
          {metrics.error && <QueryError title={t("readError")} error={metrics.error} />}
          {metrics.stale && <p role="status">{t(metrics.error ? "staleRead" : "loading")}</p>}
          {metrics.loading && !metrics.data && <p role="status">{t("loading")}</p>}
          {metrics.data && (
            <>
              <p>{t("window", { start: time(metrics.data.start), end: time(metrics.data.end) })}</p>
              <p>{t("observedAt", { time: time(metrics.data.retrievedAt) })}</p>
              <p>
                {t("samples", {
                  seconds: metrics.data.stepSeconds,
                  limit: metrics.data.sampleLimit,
                })}
              </p>
              {!metrics.data.metrics.length && <p role="status">{t("noMetrics")}</p>}
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {metrics.data.metrics.map((metric) => {
                  const value = observedValue(metric),
                    samples =
                      value === null
                        ? []
                        : metric.samples
                            .filter(
                              (sample) =>
                                Number.isFinite(sample.value) &&
                                Number.isFinite(Date.parse(sample.timestamp))
                            )
                            .map((sample) => sample.value);
                  return (
                    <StatTile
                      key={metric.key}
                      label={name(metric.key)}
                      value={value === null ? t("unknown") : number(value)}
                      footer={facts(metric)}
                      trend={
                        metric.key === "kv_cache_usage" ? (
                          <p className="text-muted-foreground text-xs">{t("kvHelp")}</p>
                        ) : undefined
                      }
                      sparkline={
                        samples.length > 1 ? (
                          <Sparkline
                            data={samples}
                            ariaLabel={t("trend", { metric: name(metric.key) })}
                          />
                        ) : undefined
                      }
                    />
                  );
                })}
              </div>
            </>
          )}
        </div>
      </Section>
      <Section
        title={`${t("density")} · ${clusterName}`}
        description={t("densityScope", { organization: organizationId })}
      >
        <div className="space-y-4">
          <Button variant="outline" asChild>
            <Link href={catalogueHref}>{t("fullCatalogue")}</Link>
          </Button>
          {density.error && <QueryError title={t("readError")} error={density.error} />}
          {density.stale && <p role="status">{t(density.error ? "staleRead" : "loading")}</p>}
          {density.loading && !density.data && <p role="status">{t("loading")}</p>}
          {density.data && (
            <>
              <p>
                {t("inventory", {
                  returned: density.data.returnedCount,
                  total: density.data.modelCount,
                  limit: density.data.inventoryLimit,
                })}
              </p>
              {density.data.truncated && <p role="status">{t("truncated")}</p>}
              <p className="break-words">
                {t("source", { source: density.data.source })} ·{" "}
                {t("observedAt", { time: time(density.data.retrievedAt) })}
              </p>
              <ListPage
                embedded
                list={list}
                label={t("label")}
                rows={density.data.items}
                stale={density.stale}
                getRowId={(row) => row.serviceId}
                rowHref={(row) => `/models/shared/${encodeURIComponent(row.serviceId)}`}
                nextCursor={null}
                totalCount={density.data.returnedCount}
                empty={{
                  icon: <BrainCircuitIcon />,
                  title: t("noModels"),
                  description: t("emptyHelp"),
                  actionHref: catalogueHref,
                  actionLabel: t("fullCatalogue"),
                }}
                columns={[
                  {
                    id: "model",
                    header: t("model"),
                    cellClassName: "max-w-48",
                    cell: (row) => <span className="break-words">{row.name}</span>,
                  },
                  {
                    id: "requests",
                    header: t("requests"),
                    cellClassName: "max-w-72",
                    cell: (row) => (
                      <div className="space-y-3">
                        <p className="font-medium">{t("desired")}</p>
                        {requests(row.desired)}
                        <p className="font-medium">{t("applied")}</p>
                        {requests(row.applied)}
                      </div>
                    ),
                  },
                  {
                    id: "observations",
                    header: t("observations"),
                    cellClassName: "max-w-80",
                    cell: (row) => (
                      <div className="space-y-3 text-xs">
                        {row.observations.map((metric) => (
                          <div key={metric.key}>
                            <p className="font-medium">
                              {name(metric.key)}: {number(observedValue(metric))}
                            </p>
                            {facts(metric)}
                          </div>
                        ))}
                      </div>
                    ),
                  },
                ]}
              />
              {capacity && (
                <div>
                  <h3 className="mb-2 font-medium">{t("capacity")}</h3>
                  <p>{t("capacityHelp")}</p>
                  <p>{t("freshness", { seconds: capacity.freshnessSeconds })}</p>
                  <DefinitionList
                    items={[
                      { term: t("capacity"), description: state(capacity.state) },
                      { term: t("cpu"), description: capacityNumber(capacity.cpuCores) },
                      { term: t("memory"), description: capacityNumber(capacity.memoryBytes) },
                      { term: t("vram"), description: capacityNumber(capacity.vramBytes) },
                      {
                        term: t("gpuCapacity"),
                        description:
                          (capacity.state === "AVAILABLE" || capacity.state === "STALE") &&
                          capacity.gpuDevices.length ? (
                            <span>
                              {capacity.gpuDevices.map((gpu) => (
                                <span key={gpu.resource} className="block break-all">
                                  {gpu.resource}: {number(gpu.devices)}
                                </span>
                              ))}
                            </span>
                          ) : (
                            t("unknown")
                          ),
                      },
                      { term: t("sourceLabel"), description: capacity.source },
                      {
                        term: t("timeLabel"),
                        description: time(capacity.observedAt),
                      },
                    ]}
                  />
                </div>
              )}
            </>
          )}
        </div>
      </Section>
    </div>
  );
}
