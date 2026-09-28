"use client";

import {
  AlertTriangleIcon,
  BellIcon,
  BellOffIcon,
  BoxIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftAppLogLine, AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

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
  // Pods
  pods: AstroliftAppPod[];
  podsLoading: boolean;
  selectedPod: string | null;
  onPickPod: (pod: string) => void;
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
   * The metric panels between the scope picker and the log viewer (golden
   * signals, endpoints, traces, PromQL, managed services, DNS / TLS /
   * workload identity), each wired to its own hook.
   */
  panels?: React.ReactNode;
  /** The alert-rules panel (#648), wired to its own hook. */
  alertRules?: React.ReactNode;
}

/**
 * Logs & metrics › Metrics (spec 44 §5.2): pods, the metric scope and its
 * panels, the scoped log in the shared LogView, platform events and alert
 * rules, each on a Panel.
 */
export function ObservabilityScreen({
  slug,
  app: a,
  loading,
  pods: podRows,
  podsLoading,
  selectedPod,
  onPickPod,
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
  panels,
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

      <PanelGrid>
        <Panel
          title={t("pods.title")}
          icon={<BoxIcon className="size-4" />}
          description={t("pods.description", { seconds: POD_POLL_MS / 1000 })}
          loading={podsLoading}
          empty={
            podRows.length === 0
              ? {
                  icon: <BoxIcon className="size-5" />,
                  title: t("pods.emptyTitle"),
                  description: t("pods.emptyDescription"),
                  actionHref: deploymentsHref,
                  actionLabel: t("pods.emptyAction"),
                }
              : null
          }
          flush
        >
          {/* The pod list is unpaginated (astroliftAppPods), and a picked row
              expands in place, which DataTable has no slot for yet. */}
          <div className="min-w-0 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("pods.columns.pod")}</TableHead>
                  <TableHead>{t("pods.columns.workload")}</TableHead>
                  <TableHead>{t("pods.columns.status")}</TableHead>
                  <TableHead className="text-right">{t("pods.columns.ready")}</TableHead>
                  <TableHead className="text-right">{t("pods.columns.restarts")}</TableHead>
                  <TableHead className="text-right">{t("pods.columns.age")}</TableHead>
                  <TableHead>{t("pods.columns.node")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {podRows.map((pod) => {
                  const readyCount = pod.containerStatuses.filter((c) => c.ready).length;
                  const total = pod.containerStatuses.length;
                  const isSelected = pod.name === selectedPod;
                  // #713: picking a pod row opens an expander under it with
                  // per-pod CPU and memory, restarts and a console deep link,
                  // as a colspan row in the same table.
                  return (
                    <React.Fragment key={pod.name}>
                      <TableRow
                        onClick={() => onPickPod(pod.name)}
                        data-selected={isSelected}
                        className="hover:bg-muted/40 data-[selected=true]:bg-muted/60 cursor-pointer"
                      >
                        <TableCell className="font-mono text-xs [overflow-wrap:anywhere] whitespace-normal">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onPickPod(pod.name);
                            }}
                            aria-pressed={isSelected}
                            className="text-left"
                          >
                            {pod.name}
                          </button>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{pod.workload || "—"}</TableCell>
                        <TableCell>
                          <span className="inline-flex items-center gap-2 text-xs">
                            <StatusDot status={statusToDot(pod.status)} />
                            <span>{pod.status}</span>
                            {pod.status !== pod.phase && pod.phase && (
                              <span className="text-muted-foreground font-mono">({pod.phase})</span>
                            )}
                          </span>
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {readyCount}/{total || 0}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {pod.restarts}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {formatAge(pod.age)}
                        </TableCell>
                        <TableCell className="text-muted-foreground font-mono text-xs">
                          {pod.node || "—"}
                        </TableCell>
                      </TableRow>
                      {isSelected ? (
                        <TableRow className="hover:bg-transparent">
                          <TableCell colSpan={7} className="p-0 whitespace-normal">
                            <PodExpander
                              appSlug={a.slug}
                              podName={pod.name}
                              defaultContainer={selectedContainer}
                              fallbackRestartCount={pod.restarts}
                              {...podUsage}
                            />
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </Panel>
      </PanelGrid>

      {/* #380 golden signals and the panels below read this scope; #422 keeps it in the URL. */}
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

      {panels}

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

      {/* #648 alert rules. */}
      {alertRules}
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
  rules: AlertRule[];
  loading: boolean;
  busy: boolean;
  creating: boolean;
  muting: boolean;
  /** Resolves true when created; the sheet closes then. */
  onCreate: (input: CreateAlertRuleInput) => Promise<boolean>;
  /** Resolves true when muted; the sheet closes then. */
  onMute: (rule: AlertRule, durationSeconds: number, reason: string) => Promise<boolean>;
  onUnmute: (rule: AlertRule) => Promise<void>;
  /** Throws on failure (the confirm dialog shows it). */
  onDelete: (rule: AlertRule) => Promise<void>;
  /** The expanded row's recent events, wired to their own hook. */
  renderEvents: (ruleId: string) => React.ReactNode;
}

/** #648 — the app's alert rules: list, expand for recent events, create / mute / delete. */
export function AlertRulesPanelView({
  appId,
  appName,
  rules: ruleList,
  loading,
  busy,
  creating,
  muting,
  onCreate,
  onMute,
  onUnmute,
  onDelete,
  renderEvents,
}: AlertRulesPanelViewProps) {
  const [createOpen, setCreateOpen] = React.useState(false);
  const [muteTarget, setMuteTarget] = React.useState<AlertRule | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AlertRule | null>(null);
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set());

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <>
      <Panel
        title="Alert rules"
        icon={<BellIcon className="size-4" />}
        description={`Thresholds that page on-call when ${appName} crosses them. Mute to silence without losing the definition; delete to retire it.`}
        actions={
          <Button size="sm" onClick={() => setCreateOpen(true)} disabled={busy}>
            <PlusIcon className="size-3.5" /> Add alert rule
          </Button>
        }
        loading={loading}
        empty={
          ruleList.length === 0
            ? {
                icon: <BellIcon className="size-5" />,
                title: "No alert rules for this app",
                description:
                  "Add a rule to page on-call when latency, error rate, or saturation crosses a threshold.",
              }
            : null
        }
        flush
      >
        <div className="min-w-0 overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Name</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ruleList.map((r) => {
                const isOpen = expanded.has(r.id);
                const sev = severityBadgeProps(r.severity);
                const muted = r.activeMute != null;
                return (
                  <React.Fragment key={r.id}>
                    <TableRow className={muted ? "opacity-75" : undefined}>
                      <TableCell className="w-8">
                        <button
                          type="button"
                          onClick={() => toggleExpanded(r.id)}
                          className="text-muted-foreground hover:text-foreground inline-flex"
                          aria-label={isOpen ? "Collapse" : "Expand"}
                          aria-expanded={isOpen}
                        >
                          {isOpen ? (
                            <ChevronDownIcon className="size-4" />
                          ) : (
                            <ChevronRightIcon className="size-4" />
                          )}
                        </button>
                      </TableCell>
                      <TableCell className="font-medium">
                        <div className="flex flex-col">
                          <span>{r.name}</span>
                          <span className="text-muted-foreground text-2xs font-mono">
                            {predicateSummary(r.predicate)}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={sev.variant} className={sev.className}>
                          {sev.label}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {muted && r.activeMute ? (
                          <Badge
                            variant="outline"
                            className="border-muted-foreground/30 text-muted-foreground"
                          >
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
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {new Date(r.createdAt).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex items-center gap-1">
                          {muted ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => void onUnmute(r)}
                              disabled={busy}
                            >
                              <Volume2Icon className="size-3.5" /> Unmute
                            </Button>
                          ) : (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setMuteTarget(r)}
                              disabled={busy}
                            >
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
                        </div>
                      </TableCell>
                    </TableRow>
                    {isOpen ? (
                      <TableRow className="hover:bg-transparent">
                        <TableCell colSpan={6} className="bg-muted/30 p-0">
                          {renderEvents(r.id)}
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </React.Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </Panel>

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
  acking: boolean;
  onAck: (event: AlertEvent) => Promise<void>;
}

/** The last few events for one alert rule, shown under its expanded row. */
export function AlertEventsListView({
  events: eventList,
  loading,
  acking,
  onAck,
}: AlertEventsListViewProps) {
  if (loading) {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }

  if (eventList.length === 0) {
    return (
      <div className="text-muted-foreground p-4 text-xs italic">
        No alert events yet for this rule.
      </div>
    );
  }

  return (
    <div className="p-4">
      <p className="text-muted-foreground text-2xs mb-2 tracking-wide uppercase">
        Last {eventList.length} event{eventList.length === 1 ? "" : "s"}
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Fired</TableHead>
            <TableHead>Resolved</TableHead>
            <TableHead>Summary</TableHead>
            <TableHead className="text-right">Ack</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {eventList.map((e) => (
            <TableRow key={e.id}>
              <TableCell className="font-mono text-xs">
                {new Date(e.firedAt).toLocaleString()}
              </TableCell>
              <TableCell className="text-muted-foreground font-mono text-xs">
                {e.resolvedAt ? new Date(e.resolvedAt).toLocaleString() : "—"}
              </TableCell>
              <TableCell className="text-xs">{e.summary || "—"}</TableCell>
              <TableCell className="text-right">
                {e.acknowledgedAt ? (
                  <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
                    <CheckIcon className="size-3.5" /> Acked
                  </span>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => void onAck(e)} disabled={acking}>
                    <CheckIcon className="size-3.5" /> Ack
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
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
