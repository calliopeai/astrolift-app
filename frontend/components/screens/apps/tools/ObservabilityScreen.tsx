"use client";

import {
  AlertTriangleIcon,
  BellIcon,
  BellOffIcon,
  BoxIcon,
  CheckIcon,
  HistoryIcon,
  InfoIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  Trash2Icon,
  Volume2Icon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { MetricScopePicker, PodEventsPanel, PodExpander } from "@/components/observability";
import type { MetricScopeOptions } from "@/components/observability/MetricScopePicker";
import type { PodEventRow } from "@/components/observability/PodEventsPanel";
import type { PodResourceUsage } from "@/components/observability/use-pod-resource-usage";
import { PageShell } from "@/components/PageShell";
import type { Column } from "@/components/data-table";
import { Feed } from "@/components/feed/Feed";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { LogView } from "@/components/run/LogView";
import {
  countLevels,
  filterLogLines,
  type LogLevelFilter,
  toLogLines,
} from "@/components/screens/apps/deployments/app-log-lines";
import { LogFilters } from "@/components/screens/apps/deployments/LogFilters";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftAppLogLine, AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import { METRICS_PANEL_LABELS, METRICS_PANELS, type MetricsPanel } from "./metrics-panels";
import type { AlertEvent, AlertRule, CreateAlertRuleInput } from "./use-alert-rules";
import {
  HISTORICAL_RANGES,
  type HistoricalRangeValue,
  LOG_BUFFER_LIMIT,
  POD_POLL_MS,
} from "./use-app-observability";

// Roll the backend's surface status string into the brand StatusDot
// palette. Anything not enumerated here renders as a muted dot so the
// row still parses visually.
function statusToDot(status: string): "ok" | "warn" | "error" | "muted" | "pending" {
  switch (status) {
    case "Running":
      return "ok";
    case "Succeeded":
      return "muted";
    case "Pending":
      return "pending";
    case "Failed":
    case "CrashLoopBackOff":
    case "ImagePullBackOff":
    case "ErrImagePull":
    case "CreateContainerConfigError":
    case "InvalidImageName":
    case "CreateContainerError":
      return "error";
    default:
      return "muted";
  }
}

function formatAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

export interface ObservabilityScreenProps {
  slug: string;
  app: Pick<AstroliftRegisteredApp, "id" | "name" | "slug"> | null;
  loading: boolean;
  /** The panel on screen (`?panel=`); only its content mounts. */
  panel: MetricsPanel;
  /** A panel's link, keeping the section and the metric scope. */
  panelHref: (panel: MetricsPanel) => string;
  // Pods (the Pods panel)
  pods: AstroliftAppPod[];
  podsLoading: boolean;
  /** The pods list: in-memory state, one page of `pods` in `podRows`. */
  podsList: ListStateController;
  podRows: AstroliftAppPod[];
  podTotal: number;
  /** A pod row's link: this panel with `?pod=<name>`, which picks it. */
  podHref: (pod: AstroliftAppPod) => string;
  selectedPod: string | null;
  podContainers: string[];
  selectedContainer: string | null;
  onPickContainer: (container: string | null) => void;
  podUsage: { usage: PodResourceUsage | null; loading: boolean; onRetry: () => void };
  // Metric scope (URL-backed)
  scopedEnv: string | null;
  scopedWorkload: string | null;
  onEnvChange: (name: string | null) => void;
  onWorkloadChange: (workload: string | null) => void;
  scopeOptions: MetricScopeOptions;
  // Logs
  logBuffer: AstroliftAppLogLine[];
  onClearLogs: () => void;
  /** Saves the whole buffer as a file. */
  onDownloadLogs: () => void;
  streaming: boolean;
  onToggleStreaming: () => void;
  allReplicas: boolean;
  onToggleAllReplicas: () => void;
  historicalRange: HistoricalRangeValue;
  onHistoricalRangeChange: (range: HistoricalRangeValue) => void;
  isHistorical: boolean;
  historicalUnavailable: boolean;
  historicalLoading: boolean;
  onRefreshHistorical: () => void;
  // Platform events
  appEvents: PodEventRow[];
  eventsLoading: boolean;
  /** Chrome-aware link for the pods empty state. */
  deploymentsHref: string;
  /** The app tab bar. */
  tabs?: React.ReactNode;
  /**
   * The Signals panel's metric panels under the scope picker (the deploy
   * summary, golden signals, endpoints, traces, PromQL, managed services),
   * each wired to its own hook. Rendered only on Signals.
   */
  signals?: React.ReactNode;
  /** The Network panel: DNS, TLS and workload identity, each on its own hook. */
  network?: React.ReactNode;
  /** The Alerts panel (#648), wired to its own hook. */
  alertRules?: React.ReactNode;
}

/**
 * Logs & metrics › Metrics (spec 44 §5.2), one panel at a time (Leo's page
 * rules 1 and 2): Signals (the metric scope and its panels), Pods (the pod
 * list, the picked pod's usage and scoped log in the shared LogView, and
 * platform events), Alert rules, and DNS & TLS. The panel is in the URL
 * (`?panel=`), and only the one on screen mounts, so only its queries run.
 */
export function ObservabilityScreen({
  slug,
  app: a,
  loading,
  panel,
  panelHref,
  pods: podRowsAll,
  podsLoading,
  podsList,
  podRows,
  podTotal,
  podHref,
  selectedPod,
  podContainers,
  selectedContainer,
  onPickContainer,
  podUsage,
  scopedEnv,
  scopedWorkload,
  onEnvChange,
  onWorkloadChange,
  scopeOptions,
  logBuffer,
  onClearLogs,
  onDownloadLogs,
  streaming,
  onToggleStreaming,
  allReplicas,
  onToggleAllReplicas,
  historicalRange,
  onHistoricalRangeChange,
  isHistorical,
  historicalUnavailable,
  historicalLoading,
  onRefreshHistorical,
  appEvents,
  eventsLoading,
  deploymentsHref,
  tabs,
  signals,
  network,
  alertRules,
}: ObservabilityScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.observability");

  if (loading) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  const picked = podRowsAll.find((p) => p.name === selectedPod) ?? null;

  const podColumns: Column<AstroliftAppPod>[] = [
    {
      id: "pod",
      header: t("pods.columns.pod"),
      sortKey: "name",
      cellClassName: "font-mono text-xs [overflow-wrap:anywhere] whitespace-normal",
      cell: (pod) => pod.name,
    },
    {
      id: "workload",
      header: t("pods.columns.workload"),
      sortKey: "workload",
      cellClassName: "font-mono text-xs",
      cell: (pod) => pod.workload || "—",
    },
    {
      id: "status",
      header: t("pods.columns.status"),
      sortKey: "status",
      cell: (pod) => (
        <span className="inline-flex items-center gap-2 text-xs">
          <StatusDot status={statusToDot(pod.status)} />
          <span>{pod.status}</span>
          {pod.status !== pod.phase && pod.phase && (
            <span className="text-muted-foreground font-mono">({pod.phase})</span>
          )}
        </span>
      ),
    },
    {
      id: "ready",
      header: t("pods.columns.ready"),
      align: "right",
      cellClassName: "font-mono text-xs",
      cell: (pod) =>
        `${pod.containerStatuses.filter((c) => c.ready).length}/${pod.containerStatuses.length || 0}`,
    },
    {
      id: "restarts",
      header: t("pods.columns.restarts"),
      sortKey: "restarts",
      align: "right",
      cellClassName: "font-mono text-xs",
      cell: (pod) => pod.restarts,
    },
    {
      id: "age",
      header: t("pods.columns.age"),
      sortKey: "age",
      align: "right",
      cellClassName: "font-mono text-xs",
      cell: (pod) => formatAge(pod.age),
    },
    {
      id: "node",
      header: t("pods.columns.node"),
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (pod) => pod.node || "—",
    },
  ];

  const scopePicker = (
    // #380 golden signals and the panels below read this scope; #422 keeps it in the URL.
    <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
      <h3 className="text-base font-medium">{t("scope.title")}</h3>
      <MetricScopePicker
        environmentName={scopedEnv}
        workloadSlug={scopedWorkload}
        onEnvironmentChange={onEnvChange}
        onWorkloadChange={onWorkloadChange}
        labels={{
          environment: t("scope.environment"),
          workload: t("scope.workload"),
          allWorkloads: t("scope.allWorkloads"),
          environmentPlaceholder: t("scope.environmentPlaceholder"),
          workloadPlaceholder: t("scope.workloadPlaceholder"),
        }}
        {...scopeOptions}
      />
    </div>
  );

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      {tabs}

      <nav aria-label={t("title", { name: a.name })} className="min-w-0">
        <ul className="bg-muted/40 inline-flex max-w-full min-w-0 flex-wrap gap-1 rounded-md border p-1">
          {METRICS_PANELS.map((p) => (
            <li key={p} className="min-w-0">
              <Link
                href={panelHref(p)}
                aria-current={p === panel ? "page" : undefined}
                className={cn(
                  "block rounded-sm px-2.5 py-1 text-sm transition-colors",
                  p === panel
                    ? "bg-background text-foreground font-medium shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {METRICS_PANEL_LABELS[p]}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      {panel === "signals" ? (
        <>
          {scopePicker}
          {signals}
        </>
      ) : panel === "network" ? (
        network
      ) : panel === "alerts" ? (
        alertRules
      ) : (
        <>
          <ListPage<AstroliftAppPod>
            embedded
            list={podsList}
            label={t("pods.title")}
            columns={podColumns}
            rows={podRows}
            getRowId={(pod) => pod.name}
            rowHref={podHref}
            rowClassName={(pod) => (pod.name === selectedPod ? "bg-muted/50" : undefined)}
            loading={podsLoading}
            totalCount={podTotal}
            empty={{
              icon: <BoxIcon className="size-5" />,
              title: t("pods.emptyTitle"),
              description: t("pods.emptyDescription"),
              actionHref: deploymentsHref,
              actionLabel: t("pods.emptyAction"),
            }}
          />

          {picked ? (
            // #713: the picked pod's CPU and memory, restarts and a console
            // deep link, under the list rather than inside a table row.
            <PanelGrid>
              <Panel
                title={picked.name}
                icon={<BoxIcon className="size-4" />}
                description={t("pods.description", { seconds: POD_POLL_MS / 1000 })}
                flush
              >
                <PodExpander
                  appSlug={a.slug}
                  podName={picked.name}
                  defaultContainer={selectedContainer}
                  fallbackRestartCount={picked.restarts}
                  {...podUsage}
                />
              </Panel>
            </PanelGrid>
          ) : null}

          {scopePicker}

          <ScopedLogs
            appSlug={a.slug}
            logBuffer={logBuffer}
            onClearLogs={onClearLogs}
            onDownloadLogs={onDownloadLogs}
            streaming={streaming}
            onToggleStreaming={onToggleStreaming}
            allReplicas={allReplicas}
            onToggleAllReplicas={onToggleAllReplicas}
            historicalRange={historicalRange}
            onHistoricalRangeChange={onHistoricalRangeChange}
            isHistorical={isHistorical}
            historicalUnavailable={historicalUnavailable}
            historicalLoading={historicalLoading}
            onRefreshHistorical={onRefreshHistorical}
            selectedPod={selectedPod}
            podContainers={podContainers}
            selectedContainer={selectedContainer}
            onPickContainer={onPickContainer}
          />

          {/* #422 platform events: auto-expands on warnings. */}
          <PodEventsPanel appEvents={appEvents} loading={eventsLoading} />
        </>
      )}
    </PageShell>
  );
}

interface ScopedLogsProps extends Pick<
  ObservabilityScreenProps,
  | "logBuffer"
  | "onClearLogs"
  | "onDownloadLogs"
  | "streaming"
  | "onToggleStreaming"
  | "allReplicas"
  | "onToggleAllReplicas"
  | "historicalRange"
  | "onHistoricalRangeChange"
  | "isHistorical"
  | "historicalUnavailable"
  | "historicalLoading"
  | "onRefreshHistorical"
  | "selectedPod"
  | "podContainers"
  | "selectedContainer"
  | "onPickContainer"
> {
  appSlug: string;
}

const ALL_CONTAINERS = "__all__";

/**
 * The scoped log in the shared LogView (spec 44 §5.5): the picked pod live,
 * every replica live (#482), or a past window (#482). Lines from many pods
 * carry the pod name in front.
 */
function ScopedLogs({
  appSlug,
  logBuffer,
  onClearLogs,
  onDownloadLogs,
  streaming,
  onToggleStreaming,
  allReplicas,
  onToggleAllReplicas,
  historicalRange,
  onHistoricalRangeChange,
  isHistorical,
  historicalUnavailable,
  historicalLoading,
  onRefreshHistorical,
  selectedPod,
  podContainers,
  selectedContainer,
  onPickContainer,
}: ScopedLogsProps) {
  const t = useTranslations("apps.observability");
  const tViewer = useTranslations("apps.logViewer");
  const [level, setLevel] = React.useState<LogLevelFilter>("all");
  const [query, setQuery] = React.useState("");
  const many = allReplicas || isHistorical;

  const mapped = React.useMemo(
    () => toLogLines(logBuffer, { withPodName: many }),
    [logBuffer, many]
  );
  const shown = React.useMemo(() => filterLogLines(mapped, level, query), [mapped, level, query]);
  const counts = React.useMemo(() => countLevels(mapped), [mapped]);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
        {isHistorical ? (
          t("logs.historicalDescription", { range: historicalRange })
        ) : allReplicas ? (
          t("logs.allReplicasDescription")
        ) : selectedPod ? (
          <span className="font-mono text-xs">
            {t("logs.streaming")} {selectedPod} {t("logs.fromCluster")}
          </span>
        ) : (
          t("logs.selectPrompt")
        )}
      </p>
      <LogFilters
        level={level}
        onLevelChange={setLevel}
        query={query}
        onQueryChange={setQuery}
        counts={counts}
      >
        {/* #482: all replicas or one pod. Off in historical mode, which
            always reads the whole replica set. */}
        <Button
          size="sm"
          variant={allReplicas ? "default" : "outline"}
          onClick={onToggleAllReplicas}
          disabled={isHistorical}
          aria-pressed={allReplicas}
        >
          <BoxIcon className="size-3.5" /> {t("logs.allReplicas")}
        </Button>
        {/* #482 time range: Live streams, any other window pages the
            aggregator. #649: the tooltip says what the window covers. */}
        <div className="flex items-center gap-1">
          <Select
            value={historicalRange}
            onValueChange={(v) => onHistoricalRangeChange(v as HistoricalRangeValue)}
          >
            <SelectTrigger size="sm" className="font-mono text-xs" aria-label="Time range">
              <HistoryIcon className="size-3.5" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HISTORICAL_RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {t(`logs.range.${r.value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground inline-flex size-7 items-center justify-center rounded-md"
                  aria-label="Retention scope"
                >
                  <InfoIcon className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-sm">
                Applies to logs, metrics, traces, and audit events. Data is stored in your cloud
                account (CloudWatch, Cloud Logging, Log Analytics, or Loki depending on your
                provider).
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
        {!many && podContainers.length > 1 && (
          <Select
            value={selectedContainer ?? ALL_CONTAINERS}
            onValueChange={(v) => onPickContainer(v === ALL_CONTAINERS ? null : v)}
          >
            <SelectTrigger
              size="sm"
              aria-label={tViewer("containerLabel")}
              className="max-w-full min-w-0 font-mono text-xs"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_CONTAINERS}>{tViewer("containerAll")}</SelectItem>
              {podContainers.map((c) => (
                <SelectItem key={c} value={c} className="font-mono">
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </LogFilters>

      {/* No aggregator on this cluster: say so before the empty pane. */}
      {isHistorical && historicalUnavailable ? (
        <div
          role="status"
          className="border-warning-border bg-warning/10 text-warning-fg flex min-w-0 items-start gap-2 rounded-md border p-2 text-xs"
        >
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
          <div className="min-w-0">
            <p className="font-medium">{t("logs.historicalUnavailableTitle")}</p>
            <p className="text-muted-foreground">{t("logs.historicalUnavailableHint")}</p>
          </div>
        </div>
      ) : null}

      <LogView
        title={t("logs.title")}
        lines={shown}
        loading={isHistorical && historicalLoading}
        onDownload={onDownloadLogs}
        emptyHint={
          shown.length !== mapped.length
            ? tViewer("filteredEmpty")
            : isHistorical
              ? historicalUnavailable
                ? t("logs.historicalUnavailableEmpty")
                : historicalLoading
                  ? t("logs.waiting")
                  : t("logs.historicalNoResults")
              : streaming
                ? t("logs.waiting")
                : allReplicas
                  ? t("logs.pressStreamAll")
                  : selectedPod
                    ? t("logs.pressStream")
                    : t("logs.pickPod")
        }
        actions={
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={onClearLogs}
              disabled={logBuffer.length === 0}
            >
              <Trash2Icon className="size-3.5" /> {t("logs.clear")}
            </Button>
            {/* Live streams or pauses; a past window refetches instead. */}
            {isHistorical ? (
              <Button size="sm" onClick={onRefreshHistorical} disabled={historicalLoading}>
                <HistoryIcon className="size-3.5" /> {t("logs.refreshHistorical")}
              </Button>
            ) : (
              <Button
                size="sm"
                variant={streaming ? "outline" : "default"}
                onClick={onToggleStreaming}
                disabled={!allReplicas && !selectedPod}
              >
                {streaming ? (
                  <>
                    <PauseIcon className="size-3.5" /> {t("logs.pause")}
                  </>
                ) : (
                  <>
                    <PlayIcon className="size-3.5" /> {t("logs.stream")}
                  </>
                )}
              </Button>
            )}
          </>
        }
      />
      <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
        {t("logs.bufferCap", { limit: LOG_BUFFER_LIMIT })}{" "}
        <code className="font-mono">astro logs --app={appSlug} --follow</code>.{" "}
        <Link href="/downloads" className="underline">
          {t("logs.installCli")}
        </Link>
        .
      </p>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// #648 — Alerts panel
// ───────────────────────────────────────────────────────────────────────────

// Operator-facing predicate presets. Each maps to the
// {metric, comparator, threshold} JSON shape the backend stores on
// AlertRule.predicate (see astrolift_operations/models/alert.py and
// the existing test_alert_rule_mutations.py fixtures).
const PREDICATE_PRESETS = [
  {
    value: "latency_p99",
    label: "p99 latency > N ms",
    metric: "latency_p99",
    comparator: ">",
    unit: "ms",
    defaultThreshold: 500,
  },
  {
    value: "error_rate_5min",
    label: "5xx error rate (5m) > N %",
    metric: "error_rate_5min",
    comparator: ">",
    unit: "%",
    defaultThreshold: 1,
  },
  {
    value: "cpu_pct",
    label: "CPU saturation > N %",
    metric: "cpu_pct",
    comparator: ">",
    unit: "%",
    defaultThreshold: 80,
  },
  {
    value: "memory_pct",
    label: "Memory saturation > N %",
    metric: "memory_pct",
    comparator: ">",
    unit: "%",
    defaultThreshold: 80,
  },
] as const;

type PredicateValue = (typeof PREDICATE_PRESETS)[number]["value"];

const SEVERITIES = [
  { value: "critical", label: "Critical" },
  { value: "warning", label: "Warning" },
  { value: "info", label: "Info" },
] as const;

const NOTIFY_CHANNELS = [
  { kind: "in_app", label: "In-app" },
  { kind: "email", label: "Email" },
  { kind: "pagerduty", label: "PagerDuty" },
] as const;

type NotifyChannelKind = (typeof NOTIFY_CHANNELS)[number]["kind"];

// Severity → Badge tone. The site's badge variants don't include a
// dedicated "warning" tone, so warning gets an inline amber class while
// critical leans on the destructive variant.
function severityBadgeProps(severity: string): {
  variant: "default" | "secondary" | "destructive" | "outline";
  className?: string;
  label: string;
} {
  switch (severity) {
    case "critical":
    case "error":
      return { variant: "destructive", label: severity };
    case "warning":
    case "warn":
      return {
        variant: "outline",
        className: "border-warning-border bg-warning/10 text-warning-fg",
        label: severity,
      };
    case "info":
    default:
      return {
        variant: "outline",
        className: "border-info-border bg-info/10 text-info-fg",
        label: severity || "info",
      };
  }
}

function formatRemaining(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  if (hours < 24) return remMinutes > 0 ? `${hours}h ${remMinutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`;
}

function predicateSummary(predicate: Record<string, unknown>): string {
  const metric = typeof predicate.metric === "string" ? predicate.metric : null;
  const op =
    typeof predicate.comparator === "string"
      ? predicate.comparator
      : typeof predicate.op === "string"
        ? predicate.op
        : null;
  const threshold =
    typeof predicate.threshold === "number" || typeof predicate.threshold === "string"
      ? predicate.threshold
      : null;
  if (metric && op != null && threshold != null) {
    return `${metric} ${op} ${threshold}`;
  }
  return JSON.stringify(predicate);
}

export interface AlertRulesPanelViewProps {
  appId: string;
  appName: string;
  /** Every rule for the app: for the count, and the picked rule's lookup. */
  rules: AlertRule[];
  /** The list: in-memory state, one page of `rules` in `rows`. */
  list: ListStateController;
  rows: AlertRule[];
  totalCount: number;
  loading: boolean;
  busy: boolean;
  creating: boolean;
  muting: boolean;
  /** The rule in `?rule=`, whose events show under the list. */
  pickedRuleId: string | null;
  /** A rule row's link: this panel with `?rule=<id>`, which picks it. */
  ruleHref: (rule: AlertRule) => string;
  /** Resolves true when created; the sheet closes then. */
  onCreate: (input: CreateAlertRuleInput) => Promise<boolean>;
  /** Resolves true when muted; the sheet closes then. */
  onMute: (rule: AlertRule, durationSeconds: number, reason: string) => Promise<boolean>;
  onUnmute: (rule: AlertRule) => Promise<void>;
  /** Throws on failure (the confirm dialog shows it). */
  onDelete: (rule: AlertRule) => Promise<void>;
  /** The picked rule's events, wired to their own hook. */
  renderEvents: (ruleId: string) => React.ReactNode;
}

/**
 * #648: the app's alert rules on the embedded list (the panel's one list),
 * the picked rule's events as a Feed under it, and create / mute / delete.
 */
export function AlertRulesPanelView({
  appId,
  appName,
  rules: ruleList,
  list,
  rows,
  totalCount,
  loading,
  busy,
  creating,
  muting,
  pickedRuleId,
  ruleHref,
  onCreate,
  onMute,
  onUnmute,
  onDelete,
  renderEvents,
}: AlertRulesPanelViewProps) {
  const [createOpen, setCreateOpen] = React.useState(false);
  const [muteTarget, setMuteTarget] = React.useState<AlertRule | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AlertRule | null>(null);
  const picked = ruleList.find((r) => r.id === pickedRuleId) ?? null;

  const columns: Column<AlertRule>[] = [
    {
      id: "name",
      header: "Name",
      sortKey: "name",
      cellClassName: "whitespace-normal",
      cell: (r) => (
        <span className="flex min-w-0 flex-col">
          <span className="font-medium [overflow-wrap:anywhere]">{r.name}</span>
          <span className="text-muted-foreground text-2xs font-mono [overflow-wrap:anywhere]">
            {predicateSummary(r.predicate)}
          </span>
        </span>
      ),
    },
    {
      id: "severity",
      header: "Severity",
      sortKey: "severity",
      cell: (r) => {
        const sev = severityBadgeProps(r.severity);
        return (
          <Badge variant={sev.variant} className={sev.className}>
            {sev.label}
          </Badge>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      cell: (r) =>
        r.activeMute ? (
          <Badge variant="outline" className="border-muted-foreground/30 text-muted-foreground">
            Muted · {formatRemaining(r.activeMute.ttlUntil)}
          </Badge>
        ) : r.isActive ? (
          <span className="inline-flex items-center gap-1.5 text-xs">
            <StatusDot status="ok" />
            Active
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs">
            <StatusDot status="muted" />
            Inactive
          </span>
        ),
    },
    {
      id: "created",
      header: "Created",
      sortKey: "created",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (r) => new Date(r.createdAt).toLocaleDateString(),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      // Above the row's stretched link, so these act instead of picking the row.
      cellClassName: "relative z-10",
      cell: (r) => (
        <span className="inline-flex items-center gap-1">
          {r.activeMute ? (
            <Button variant="ghost" size="sm" onClick={() => void onUnmute(r)} disabled={busy}>
              <Volume2Icon className="size-3.5" /> Unmute
            </Button>
          ) : (
            <Button variant="ghost" size="sm" onClick={() => setMuteTarget(r)} disabled={busy}>
              <BellOffIcon className="size-3.5" /> Mute
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setDeleteTarget(r)}
            disabled={busy}
            aria-label={`Delete ${r.name}`}
          >
            <Trash2Icon className="size-3.5" />
          </Button>
        </span>
      ),
    },
  ];

  return (
    <>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <p className="text-muted-foreground min-w-0 flex-1 text-sm">
          Thresholds that page on-call when {appName} crosses them. Mute to silence without losing
          the definition; delete to retire it.
        </p>
        <Button size="sm" onClick={() => setCreateOpen(true)} disabled={busy}>
          <PlusIcon className="size-3.5" /> Add alert rule
        </Button>
      </div>

      <ListPage<AlertRule>
        embedded
        list={list}
        label="Alert rules"
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        rowHref={ruleHref}
        rowClassName={(r) =>
          cn(r.activeMute && "opacity-75", r.id === pickedRuleId && "bg-muted/50") || undefined
        }
        loading={loading}
        totalCount={totalCount}
        empty={{
          icon: <BellIcon className="size-5" />,
          title: "No alert rules for this app",
          description:
            "Add a rule to page on-call when latency, error rate, or saturation crosses a threshold.",
        }}
      />

      {picked ? (
        <PanelGrid>
          <Panel
            title={`Events · ${picked.name}`}
            icon={<BellIcon className="size-4" />}
            description={predicateSummary(picked.predicate)}
          >
            {renderEvents(picked.id)}
          </Panel>
        </PanelGrid>
      ) : null}

      <CreateAlertRuleSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        appId={appId}
        busy={creating}
        onSubmit={async (input) => {
          const ok = await onCreate(input);
          if (ok) setCreateOpen(false);
          return ok;
        }}
      />

      <MuteAlertRuleSheet
        key={muteTarget?.id ?? "none"}
        target={muteTarget}
        onOpenChange={(next) => {
          if (!next) setMuteTarget(null);
        }}
        busy={muting}
        onSubmit={async (durationSeconds, reason) => {
          if (!muteTarget) return;
          if (await onMute(muteTarget, durationSeconds, reason)) setMuteTarget(null);
        }}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={deleteTarget ? `Delete ${deleteTarget.name}?` : "Delete alert rule?"}
        description="This removes the rule and stops future notifications. Existing alert events stay in history."
        confirmLabel="Delete rule"
        destructive
        onConfirm={async () => {
          if (!deleteTarget) return;
          await onDelete(deleteTarget);
        }}
      />
    </>
  );
}

export interface AlertEventsListViewProps {
  events: AlertEvent[];
  loading: boolean;
  error?: string | null;
  onRetry?: () => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  acking: boolean;
  onAck: (event: AlertEvent) => Promise<void>;
}

/**
 * One alert rule's events, newest first, as a Feed (Leo's list rule 5): it
 * scrolls in its own frame, grouped by day, and loads older events on the
 * cursor as the reader nears the end.
 */
export function AlertEventsListView({
  events: eventList,
  loading,
  error,
  onRetry,
  hasMore,
  loadingMore,
  onLoadMore,
  acking,
  onAck,
}: AlertEventsListViewProps) {
  return (
    <Feed
      label="Alert events"
      items={eventList}
      keyOf={(e) => e.id}
      groupBy={{ day: (e) => e.firedAt }}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{ icon: <BellIcon className="size-5" />, title: "No alert events yet for this rule" }}
      hasMore={hasMore}
      loadingMore={loadingMore}
      onLoadMore={onLoadMore}
      maxHeight="max-h-80"
      dense
      renderItem={(e) => (
        <div className="flex min-w-0 items-start gap-3 text-xs">
          <div className="min-w-0 flex-1 space-y-0.5">
            <p className="[overflow-wrap:anywhere]">{e.summary || "—"}</p>
            <p className="text-muted-foreground font-mono">
              {new Date(e.firedAt).toLocaleString()}
              {e.resolvedAt ? ` → resolved ${new Date(e.resolvedAt).toLocaleString()}` : ""}
            </p>
          </div>
          {e.acknowledgedAt ? (
            <span className="text-muted-foreground inline-flex shrink-0 items-center gap-1">
              <CheckIcon className="size-3.5" /> Acked
            </span>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              className="shrink-0"
              onClick={() => void onAck(e)}
              disabled={acking}
            >
              <CheckIcon className="size-3.5" /> Ack
            </Button>
          )}
        </div>
      )}
    />
  );
}

function CreateAlertRuleSheet({
  open,
  onOpenChange,
  appId,
  busy,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  appId: string;
  busy: boolean;
  onSubmit: (input: CreateAlertRuleInput) => Promise<boolean>;
}) {
  const [name, setName] = React.useState("");
  const [severity, setSeverity] = React.useState<string>("warning");
  const [predicateKind, setPredicateKind] = React.useState<PredicateValue>("latency_p99");
  const [threshold, setThreshold] = React.useState<string>("500");
  const [channelKinds, setChannelKinds] = React.useState<Set<NotifyChannelKind>>(
    () => new Set<NotifyChannelKind>(["in_app"])
  );
  const [channelAddresses, setChannelAddresses] = React.useState<
    Partial<Record<NotifyChannelKind, string>>
  >({});

  // Reset when the sheet closes so a re-open starts clean. We track the
  // open prop locally because the parent owns the value.
  const [prevOpen, setPrevOpen] = React.useState(open);
  if (prevOpen !== open) {
    setPrevOpen(open);
    if (!open) {
      setName("");
      setSeverity("warning");
      setPredicateKind("latency_p99");
      setThreshold("500");
      setChannelKinds(new Set<NotifyChannelKind>(["in_app"]));
      setChannelAddresses({});
    } else {
      const preset = PREDICATE_PRESETS.find((p) => p.value === "latency_p99");
      if (preset) setThreshold(String(preset.defaultThreshold));
    }
  }

  const currentPreset = PREDICATE_PRESETS.find((p) => p.value === predicateKind)!;

  function togglePredicate(value: PredicateValue) {
    const preset = PREDICATE_PRESETS.find((p) => p.value === value);
    setPredicateKind(value);
    if (preset) setThreshold(String(preset.defaultThreshold));
  }

  function toggleChannel(kind: NotifyChannelKind) {
    setChannelKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const n = Number(threshold);
    if (!name.trim() || !Number.isFinite(n)) return;
    if (channelKinds.size === 0) {
      toast.error("Pick at least one notification channel");
      return;
    }
    const predicate = {
      metric: currentPreset.metric,
      comparator: currentPreset.comparator,
      threshold: n,
      unit: currentPreset.unit,
    };
    const notifyChannels = Array.from(channelKinds).map((kind) => ({
      kind,
      ref: channelAddresses[kind]?.trim() || "",
    }));
    await onSubmit({
      name: name.trim(),
      target: "app",
      targetId: appId,
      severity,
      predicate,
      notifyChannels,
      isActive: true,
    });
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-md">
        <SheetHeader>
          <SheetTitle>New alert rule</SheetTitle>
          <SheetDescription>
            Pages on-call when the chosen metric crosses the threshold. Routing fan-out is taken
            from the notify channels you select below.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="ar-name">Name</Label>
            <Input
              id="ar-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="High p99 latency"
              required
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-severity">Severity</Label>
            <Select value={severity} onValueChange={setSeverity}>
              <SelectTrigger id="ar-severity">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SEVERITIES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-predicate">Condition</Label>
            <Select
              value={predicateKind}
              onValueChange={(v) => togglePredicate(v as PredicateValue)}
            >
              <SelectTrigger id="ar-predicate">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PREDICATE_PRESETS.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-threshold">Threshold ({currentPreset.unit})</Label>
            <Input
              id="ar-threshold"
              type="number"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              min={0}
              step="any"
              required
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label>Notify channels</Label>
            <div className="grid gap-2">
              {NOTIFY_CHANNELS.map((c) => {
                const enabled = channelKinds.has(c.kind);
                const needsAddress = c.kind === "email" || c.kind === "pagerduty";
                return (
                  <div
                    key={c.kind}
                    className="border-input flex flex-col gap-2 rounded-md border p-2"
                  >
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={() => toggleChannel(c.kind)}
                        className="accent-primary size-4"
                      />
                      <span>{c.label}</span>
                    </label>
                    {enabled && needsAddress ? (
                      <Input
                        value={channelAddresses[c.kind] ?? ""}
                        onChange={(e) =>
                          setChannelAddresses((prev) => ({ ...prev, [c.kind]: e.target.value }))
                        }
                        placeholder={
                          c.kind === "email" ? "oncall@acme.com" : "pagerduty service key"
                        }
                        className="font-mono text-xs"
                      />
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Creating…" : "Create rule"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function MuteAlertRuleSheet({
  target,
  onOpenChange,
  onSubmit,
  busy,
}: {
  target: AlertRule | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (durationSeconds: number, reason: string) => Promise<void>;
  busy: boolean;
}) {
  const [hours, setHours] = React.useState("2");
  const [reason, setReason] = React.useState("");

  return (
    <Sheet open={target !== null} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{target ? `Mute ${target.name}` : "Mute alert rule"}</SheetTitle>
          <SheetDescription>
            Silences notifications for the chosen window. The rule stays evaluated; only routing is
            suppressed.
          </SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            const h = Number(hours);
            if (!Number.isFinite(h) || h < 1 || h > 72) return;
            if (!reason.trim()) return;
            await onSubmit(Math.round(h * 3600), reason.trim());
          }}
          className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="mute-hours">Duration (hours, 1–72)</Label>
            <Input
              id="mute-hours"
              type="number"
              min={1}
              max={72}
              step={1}
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="mute-reason">Reason</Label>
            <Textarea
              id="mute-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Known noisy after deploy; revisit Friday."
              rows={4}
              required
            />
            <p className="text-muted-foreground text-xs">
              Logged on the rule&apos;s audit trail so the next operator sees who muted it and why.
            </p>
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !reason.trim()}>
              {busy ? "Muting…" : "Mute rule"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
