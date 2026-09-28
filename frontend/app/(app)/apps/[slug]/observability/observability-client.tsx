"use client";

import { useDnsRecords } from "@/components/observability/use-dns-records";
import { useEndpointMetrics } from "@/components/observability/use-endpoint-metrics";
import { useMetricScopeOptions } from "@/components/observability/use-metric-scope-options";
import { usePromql } from "@/components/observability/use-promql";
import { useTraceExplorer } from "@/components/observability/use-trace-explorer";
import { useTlsCertificates } from "@/components/observability/use-tls-certificates";
import { useWorkloadIdentity } from "@/components/observability/use-workload-identity";
import { useMutation, useLazyQuery, useQuery, useSubscription } from "@apollo/client/react";
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
  TerminalIcon,
  Trash2Icon,
  Volume2Icon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import {
  DnsRecordsCard,
  EndpointMetricsPanel,
  GoldenSignalsPanel,
  LogViewer,
  ManagedServiceMetricsList,
  MetricScopePicker,
  PodEventsPanel,
  PodExpander,
  PromqlQueryPanel,
  TlsCertificatesCard,
  TraceExplorerPanel,
  WorkloadIdentityCard,
} from "@/components/observability";
import type { ObservabilityPanelReason } from "@/components/observability/panel-reason";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import { ON_APP_LOG, ON_APP_LOGS } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftAppLogLine, AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ACKNOWLEDGE_ALERT_EVENT,
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_EVENTS,
  LIST_ALERT_RULES,
  MUTE_ALERT_RULE,
  UNMUTE_ALERT_RULE,
} from "@/graphql/operations/alerts.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP_LOGS } from "@/graphql/observability/observability.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface EventRow {
  id: string;
  eventType: string;
  payload: Record<string, unknown>;
  organizationId: string | null;
  registeredAppId: string | null;
  occurredAt: string;
}

interface EventsResp {
  astroliftEvents: EventRow[];
}

interface PodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

interface LogResp {
  astroliftOnAppLog: AstroliftAppLogLine;
}

// #482 — multi-pod aggregated subscription. Same line shape as the
// per-pod stream; the line's podName is what tells the operator which
// replica produced it.
interface LogsResp {
  astroliftOnAppLogs: AstroliftAppLogLine;
}

// #482 — historical page query. `historicalAvailable: false` means the
// cluster has no log-aggregator wired — the UI shows the "live tail
// only" empty state in that case. `reason` (#1111) is the fuller
// discriminator that subsumes it (NOT_CONFIGURED / NO_DATA_YET /
// ERROR / OK); `historicalAvailable` is kept for the existing badge.
interface HistoricalLogsResp {
  astroliftAppLogs: {
    items: AstroliftAppLogLine[];
    nextCursor: string;
    reachedRetention: boolean;
    historicalAvailable: boolean;
    totalCount: number;
    reason: ObservabilityPanelReason;
  };
}

// Built-in time-range presets for the historical picker — match the
// shape the golden-signals scope picker uses so operators see a
// consistent set of windows across both surfaces.
const HISTORICAL_RANGES = [
  { value: "live", seconds: 0 },
  { value: "15m", seconds: 15 * 60 },
  { value: "1h", seconds: 60 * 60 },
  { value: "6h", seconds: 6 * 60 * 60 },
  { value: "24h", seconds: 24 * 60 * 60 },
] as const;

type HistoricalRangeValue = (typeof HISTORICAL_RANGES)[number]["value"];

// Live cluster surface — pods refetch periodically as a safety net
// against missed subscription events (the deploy.lifecycle stream
// is the canonical 'something changed' signal but the runtime
// cluster itself doesn't push us pod-state events).
const POD_POLL_MS = 5000;
const LOG_BUFFER_LIMIT = 500;
const DEFAULT_TAIL_LINES = 200;

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

// Heuristic for the "default container" — pick the one whose name
// matches the pod's workload (typical for app workloads named after
// the deployment). Falls back to the first non-sidecar candidate so
// we don't auto-select istio-proxy / linkerd-proxy / otel-collector
// when they're present alongside the main app container.
const KNOWN_SIDECARS = new Set([
  "istio-proxy",
  "envoy",
  "linkerd-proxy",
  "datadog-agent",
  "otel-collector",
  "otc-container",
  "newrelic-infrastructure",
  "fluent-bit",
  "fluentd",
  "filebeat",
  "vault-agent",
  "vault-agent-init",
]);

function pickDefaultContainer(
  containers: string[],
  workload: string | null | undefined
): string | null {
  if (containers.length === 0) return null;
  if (workload) {
    const match = containers.find((c) => c === workload);
    if (match) return match;
  }
  const nonSidecar = containers.find((c) => !KNOWN_SIDECARS.has(c));
  return nonSidecar ?? containers[0];
}

// #648 — Alert-rules panel typings. Mirrors the AlertsClient at
// app/(app)/alerts/alerts-client.tsx; declared locally here because the
// repo intentionally types each consumer rather than re-exporting from
// the operations module (predicate/notifyChannels are scalar JSON on
// the wire so each surface narrows them to its own shape).
interface AlertMute {
  id: string;
  ttlUntil: string;
  reason: string;
  createdBy: string;
}

interface AlertRule {
  id: string;
  name: string;
  target: string;
  targetId: string;
  severity: string;
  predicate: Record<string, unknown>;
  notifyChannels: Array<Record<string, unknown>> | Record<string, unknown>;
  isActive: boolean;
  organizationSlug: string;
  createdAt: string;
  updatedAt: string;
  activeMute: AlertMute | null;
}

interface AlertEvent {
  id: string;
  ruleId: string;
  severity: string;
  firedAt: string;
  resolvedAt?: string | null;
  acknowledgedAt?: string | null;
  summary: string;
  detail: Record<string, unknown>;
}

interface AlertRulesResp {
  astroliftAlertRules: AlertRule[];
}

interface AlertEventsResp {
  astroliftAlertEvents: AlertEvent[];
}

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

export function ObservabilityClient({ slug }: { slug: string }) {
  const dns = useDnsRecords(slug);
  const tls = useTlsCertificates(slug);
  const identity = useWorkloadIdentity(slug);
  const chrome = useAppChrome();
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.observability");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 200 },
    pollInterval: 15000,
  });

  const pods = useQuery<PodsResp>(LIST_APP_PODS, {
    variables: { appSlug: slug },
    pollInterval: POD_POLL_MS,
  });

  // #645 / #646 — fetch the app's managed-service list so we can fan
  // out one MetricsPanel per supported binding (postgres → RDS-style
  // tile; object_store → S3-style tile). Unsupported kinds are
  // filtered out inside ManagedServiceMetricsList.
  const managedServices = useQuery<{
    astroliftManagedServices: Array<{ id: string; kind: string }>;
  }>(LIST_MANAGED_SERVICES, {
    variables: { appSlug: slug, environmentName: null },
  });

  const a = app.data?.astroliftApp;
  // Memo guards podRows so its identity is stable when the response
  // is undefined / unchanged — useEffect deps below depend on it.
  const podRows: AstroliftAppPod[] = React.useMemo(
    () => pods.data?.astroliftAppPods ?? [],
    [pods.data]
  );

  const appEvents = React.useMemo(() => {
    const all = events.data?.astroliftEvents ?? [];
    if (!a) return [];
    return all.filter((e) => e.registeredAppId === a.id);
  }, [events.data, a]);

  // Operator picks one pod to tail at a time. Track only the
  // *user-overridden* selection in state; derive the effective
  // pod for the subscription from the pod list so we don't have
  // to setState inside an effect when the list changes.
  //
  // ``?pod=<name>`` deep-links from the workload status grid
  // (#429) seed the pick once on first paint; afterwards the
  // operator's explicit pick wins so we don't fight the URL on
  // every interaction.
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const podParam = searchParams?.get("pod") ?? null;
  const envParam = searchParams?.get("env") ?? null;
  const workloadParam = searchParams?.get("workload") ?? null;
  const [pickedPod, setPickedPod] = React.useState<string | null>(podParam);
  React.useEffect(() => {
    if (podParam && pickedPod == null) {
      setPickedPod(podParam);
    }
    // We intentionally only react to the URL on mount-equivalent
    // transitions — once the operator picks a row, their pick is
    // sticky for the rest of the session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [podParam]);

  // Env + workload pickers (#422) — URL is the single source of
  // truth so deep-links survive refresh. ``null`` means "use the
  // resolver default" — backend picks the alphabetically-first env;
  // workload-null rolls every workload up. The picker component
  // renders both selects above the ``GoldenSignalsPanel``.
  const scopedEnv = envParam;
  const scopedWorkload = workloadParam;
  const scopeOptions = useMetricScopeOptions(slug);
  const traces = useTraceExplorer(slug, scopedEnv);
  const promql = usePromql(slug, scopedEnv);
  const endpointMetrics = useEndpointMetrics({
    appSlug: slug,
    environmentName: scopedEnv,
    workloadSlug: scopedWorkload,
  });

  const updateScopeParam = React.useCallback(
    (key: "env" | "workload", value: string | null) => {
      if (!pathname) return;
      const next = new URLSearchParams(searchParams?.toString() ?? "");
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams]
  );

  const handleEnvChange = React.useCallback(
    (name: string | null) => updateScopeParam("env", name),
    [updateScopeParam]
  );
  const handleWorkloadChange = React.useCallback(
    (workload: string | null) => updateScopeParam("workload", workload),
    [updateScopeParam]
  );
  const selectedPod: string | null = React.useMemo(() => {
    if (pickedPod && podRows.some((p) => p.name === pickedPod)) return pickedPod;
    const running = podRows.find((p) => p.status === "Running");
    return running?.name ?? podRows[0]?.name ?? null;
  }, [pickedPod, podRows]);

  const [streaming, setStreaming] = React.useState(false);
  const [logBuffer, setLogBuffer] = React.useState<AstroliftAppLogLine[]>([]);
  // Container list derived from the selected pod's containerStatuses.
  const podContainers: string[] = React.useMemo(() => {
    const pod = podRows.find((p) => p.name === selectedPod);
    return pod?.containerStatuses.map((c) => c.name) ?? [];
  }, [podRows, selectedPod]);
  const selectedPodWorkload = React.useMemo(
    () => podRows.find((p) => p.name === selectedPod)?.workload ?? null,
    [podRows, selectedPod]
  );

  // Operator-overridden container; null means "use the default" which
  // we recompute from the pod's containers below.
  const [pickedContainer, setPickedContainer] = React.useState<string | null>(null);
  const selectedContainer: string | null = React.useMemo(() => {
    if (pickedContainer && podContainers.includes(pickedContainer)) {
      return pickedContainer;
    }
    return pickDefaultContainer(podContainers, selectedPodWorkload);
  }, [pickedContainer, podContainers, selectedPodWorkload]);

  // Clearing the buffer when the operator switches pods or containers
  // uses the official React pattern for "reset state on a different
  // key": store the previous key in state alongside the buffer and
  // compare during render. setState in render is fine when guarded;
  // React replays the render with the new state on the same commit.
  // See: https://react.dev/learn/you-might-not-need-an-effect#resetting-all-state-when-a-prop-changes
  const streamKey = `${selectedPod ?? ""}::${selectedContainer ?? ""}`;
  const [prevStreamKey, setPrevStreamKey] = React.useState(streamKey);
  if (prevStreamKey !== streamKey) {
    setPrevStreamKey(streamKey);
    if (logBuffer.length !== 0) setLogBuffer([]);
  }

  // #482 — "All replicas" toggle. Default ON so the mobile / quick-look
  // path is the multi-pod aggregate; the existing per-pod tail stays
  // one toggle away for advanced operators who want to focus on a
  // specific replica. The toggle is local — no URL persistence —
  // because it's a viewer-mode preference, not a deep-linkable filter.
  const [allReplicas, setAllReplicas] = React.useState(true);

  // #482 — historical time-range picker. "live" keeps the streaming
  // path; any other value swaps to the paginated `astroliftAppLogs`
  // query against the cluster's log-aggregator backend.
  const [historicalRange, setHistoricalRange] = React.useState<HistoricalRangeValue>("live");
  const isHistorical = historicalRange !== "live";

  // Live per-pod log subscription — only opens while ``streaming`` is
  // true, an individual pod is picked (not "all replicas"), and the
  // operator hasn't switched into a historical window. ``skip``
  // prevents an Apollo socket from opening on initial render and on
  // Pause / mode-switch.
  useSubscription<LogResp>(ON_APP_LOG, {
    variables: {
      appSlug: slug,
      podName: selectedPod ?? "",
      container: selectedContainer ?? null,
      follow: true,
      tailLines: DEFAULT_TAIL_LINES,
    },
    skip: !streaming || !selectedPod || allReplicas || isHistorical,
    onData: ({ data }) => {
      const line = data.data?.astroliftOnAppLog;
      if (!line) return;
      setLogBuffer((prev) => {
        const next = [...prev, line];
        // Cap memory so an hours-long tail doesn't grow unbounded.
        return next.length > LOG_BUFFER_LIMIT ? next.slice(-LOG_BUFFER_LIMIT) : next;
      });
    },
  });

  // #482 — multi-pod aggregated subscription. Activates when the
  // "All replicas" toggle is on AND we're streaming AND not in
  // historical mode. Reuses the same buffer + cap so the LogViewer
  // renders the same way regardless of source.
  useSubscription<LogsResp>(ON_APP_LOGS, {
    variables: {
      appSlug: slug,
      environmentName: scopedEnv ?? null,
      workloadSlug: scopedWorkload ?? null,
      container: selectedContainer ?? null,
      follow: true,
      tailLines: DEFAULT_TAIL_LINES,
    },
    skip: !streaming || !allReplicas || isHistorical,
    onData: ({ data }) => {
      const line = data.data?.astroliftOnAppLogs;
      if (!line) return;
      setLogBuffer((prev) => {
        const next = [...prev, line];
        return next.length > LOG_BUFFER_LIMIT ? next.slice(-LOG_BUFFER_LIMIT) : next;
      });
    },
  });

  // #482 — historical paginated query. Lazy + manual trigger so we
  // don't fan a request out the moment the operator opens the page;
  // the picker / refresh button is the entry point.
  const [historicalUnavailable, setHistoricalUnavailable] = React.useState(false);
  const [fetchHistorical, historicalState] = useLazyQuery<HistoricalLogsResp>(GET_APP_LOGS, {
    fetchPolicy: "no-cache",
  });

  // Apollo's useLazyQuery types data as DeepPartial<TData>; we
  // coerce out once the top-level field is non-null + treat it as
  // the structured response (the resolver returns the page object
  // whole, not field-by-field, so the partial type is a runtime
  // mismatch with the wire shape).
  //
  // We use the during-render state-reset pattern (mirrors the
  // ``prevStreamKey`` block above) instead of a useEffect so React's
  // synchronous-setState-in-effect lint rule stays clean.
  const historicalDataToken = historicalState.data ? historicalState.data : null;
  const [prevHistoricalDataToken, setPrevHistoricalDataToken] =
    React.useState<typeof historicalDataToken>(null);
  if (prevHistoricalDataToken !== historicalDataToken) {
    setPrevHistoricalDataToken(historicalDataToken);
    if (historicalDataToken) {
      const page = historicalDataToken.astroliftAppLogs as
        | HistoricalLogsResp["astroliftAppLogs"]
        | undefined;
      if (page) {
        const unavailable = !page.historicalAvailable;
        if (historicalUnavailable !== unavailable) {
          setHistoricalUnavailable(unavailable);
        }
        // Replace the buffer wholesale — historical pages aren't
        // appended, they replace the live-tail content for the
        // duration of the historical-mode view.
        setLogBuffer(unavailable ? [] : page.items);
      }
    }
  }

  const runHistoricalQuery = React.useCallback(
    (range: HistoricalRangeValue) => {
      const def = HISTORICAL_RANGES.find((r) => r.value === range);
      if (!def || def.seconds === 0) return;
      const until = new Date();
      const since = new Date(until.getTime() - def.seconds * 1000);
      // Pass variables to the lazy fn itself — passing them in the
      // hook options doesn't trigger a refetch and Apollo v4's
      // useLazyQuery wants the call-site variables.
      void fetchHistorical({
        variables: {
          appSlug: slug,
          since: since.toISOString(),
          until: until.toISOString(),
          environmentName: scopedEnv,
          workloadSlug: scopedWorkload,
          limit: LOG_BUFFER_LIMIT,
        },
      });
    },
    [fetchHistorical, slug, scopedEnv, scopedWorkload]
  );

  // Fire one historical query when the operator switches into a
  // time-range mode (or when the env / workload scope changes while
  // in historical mode). Live mode owns its own subscription path.
  const historicalKey = `${historicalRange}::${scopedEnv ?? ""}::${scopedWorkload ?? ""}`;
  const [prevHistoricalKey, setPrevHistoricalKey] = React.useState(historicalKey);
  if (prevHistoricalKey !== historicalKey) {
    setPrevHistoricalKey(historicalKey);
    if (isHistorical) {
      // Clear before fetching so old live-tail content doesn't bleed
      // into the historical render while the request is in flight.
      setLogBuffer([]);
      runHistoricalQuery(historicalRange);
    } else {
      setHistoricalUnavailable(false);
    }
  }

  if (app.loading && !a) {
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

  const podsLoading = pods.loading && podRows.length === 0;

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="observability" />

      {/* ─── pod list ──────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BoxIcon className="size-4" /> {t("pods.title")}
          </CardTitle>
          <CardDescription>
            {t("pods.description", { seconds: POD_POLL_MS / 1000 })}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {podsLoading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : podRows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title={t("pods.emptyTitle")}
                description={t("pods.emptyDescription")}
                actionHref={appPath(chrome, a.slug, "deployments")}
                actionLabel={t("pods.emptyAction")}
              />
            </div>
          ) : (
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
                  // #713 — when the operator picks a pod row, an
                  // expander follows immediately under it with
                  // per-pod CPU + mem sparkline, restart count, and
                  // an Open-in-Console deep link. The expander is
                  // an extra TableRow with colspan so it lives in
                  // the same table semantics (no separate widget
                  // breaking the row striping).
                  return (
                    <React.Fragment key={pod.name}>
                      <TableRow
                        onClick={() => setPickedPod(pod.name)}
                        data-selected={isSelected}
                        className="hover:bg-muted/40 data-[selected=true]:bg-muted/60 cursor-pointer"
                      >
                        <TableCell className="font-mono text-xs">{pod.name}</TableCell>
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
                          <TableCell colSpan={7} className="p-0">
                            <PodExpander
                              appSlug={a.slug}
                              podName={pod.name}
                              environmentName={scopedEnv}
                              defaultContainer={selectedContainer}
                              fallbackRestartCount={pod.restarts}
                            />
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ─── #380 SRE golden signals + status-code breakdown ───────────── */}
      {/* #422 env + workload pickers above the panel; persists via URL. */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-medium">{t("scope.title")}</h3>
        <MetricScopePicker
          environmentName={scopedEnv}
          workloadSlug={scopedWorkload}
          onEnvironmentChange={handleEnvChange}
          onWorkloadChange={handleWorkloadChange}
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
      <GoldenSignalsPanel
        appSlug={a.slug}
        environmentName={scopedEnv}
        workloadSlug={scopedWorkload}
      />

      {/* ─── #641 per-endpoint HTTP metrics ────────────────────────── */}
      <EndpointMetricsPanel {...endpointMetrics} />

      {/* ─── #644 distributed trace explorer ───────────────────────── */}
      <TraceExplorerPanel {...traces} />

      {/* ─── #647 ad-hoc PromQL panel ──────────────────────────────── */}
      <PromqlQueryPanel {...promql} />

      {/* ─── #645 / #646 managed-service metric tiles ─────────────── */}
      <ManagedServiceMetricsList
        managedServices={managedServices.data?.astroliftManagedServices ?? []}
      />

      {/* ─── #377 observability cards (DNS / TLS / Workload identity) ── */}
      <DnsRecordsCard appSlug={a.slug} {...dns} />
      <TlsCertificatesCard appSlug={a.slug} {...tls} />
      <WorkloadIdentityCard appSlug={a.slug} {...identity} />

      {/* ─── log viewer ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <TerminalIcon className="size-4" /> {t("logs.title")}
            </CardTitle>
            <CardDescription>
              {isHistorical ? (
                t("logs.historicalDescription", { range: historicalRange })
              ) : allReplicas ? (
                t("logs.allReplicasDescription")
              ) : selectedPod ? (
                <>
                  {t("logs.streaming")}{" "}
                  <code className="bg-muted text-2xs rounded px-1 py-0.5 font-mono">
                    {selectedPod}
                  </code>{" "}
                  {t("logs.fromCluster")}
                </>
              ) : (
                t("logs.selectPrompt")
              )}
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {/* #482 — aggregated-vs-per-pod toggle. Disabled in
                historical mode because the historical query always
                runs against the whole replica set. */}
            <Button
              size="sm"
              variant={allReplicas ? "default" : "outline"}
              onClick={() => setAllReplicas((v) => !v)}
              disabled={isHistorical}
              aria-pressed={allReplicas}
            >
              <BoxIcon className="size-3" /> {t("logs.allReplicas")}
            </Button>

            {/* #482 — time-range picker. "Live" keeps the streaming
                path; any other value swaps to the paginated query.
                #649 — retention scope tooltip clarifies which signals
                the window covers + where the data lives. */}
            <div className="flex items-center gap-1">
              <Select
                value={historicalRange}
                onValueChange={(v) => setHistoricalRange(v as HistoricalRangeValue)}
              >
                <SelectTrigger size="sm" className="font-mono text-xs">
                  <HistoryIcon className="size-3" />
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

            <Button
              size="sm"
              variant="outline"
              onClick={() => setLogBuffer([])}
              disabled={logBuffer.length === 0}
            >
              <Trash2Icon className="size-3" /> {t("logs.clear")}
            </Button>

            {/* Live-mode stream/pause; historical mode shows a
                refetch button instead. */}
            {isHistorical ? (
              <Button
                size="sm"
                variant="default"
                onClick={() => runHistoricalQuery(historicalRange)}
                disabled={historicalState.loading}
              >
                <HistoryIcon className="size-3" /> {t("logs.refreshHistorical")}
              </Button>
            ) : (
              <Button
                size="sm"
                variant={streaming ? "outline" : "default"}
                onClick={() => setStreaming((s) => !s)}
                disabled={!allReplicas && !selectedPod}
              >
                {streaming ? (
                  <>
                    <PauseIcon className="size-3" /> {t("logs.pause")}
                  </>
                ) : (
                  <>
                    <PlayIcon className="size-3" /> {t("logs.stream")}
                  </>
                )}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {/* Historical-mode badge — surfaces "live tail only on this
              cluster" when the aggregator backend isn't configured.
              We render before the LogViewer so the operator sees the
              reason for the empty pane immediately. */}
          {isHistorical && historicalUnavailable ? (
            <div className="border-warning-border bg-warning/10 text-warning-fg mb-3 flex items-start gap-2 rounded-md border p-2 text-xs">
              <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
              <div>
                <p className="font-medium">{t("logs.historicalUnavailableTitle")}</p>
                <p className="text-muted-foreground">{t("logs.historicalUnavailableHint")}</p>
              </div>
            </div>
          ) : null}
          <LogViewer
            lines={logBuffer}
            appSlug={a.slug}
            environmentName={scopedEnv}
            podName={allReplicas || isHistorical ? null : selectedPod}
            containers={allReplicas || isHistorical ? [] : podContainers}
            selectedContainer={allReplicas || isHistorical ? null : selectedContainer}
            onContainerChange={allReplicas || isHistorical ? undefined : setPickedContainer}
            bufferLimit={LOG_BUFFER_LIMIT}
            showPodBadge={allReplicas || isHistorical}
            loading={isHistorical && historicalState.loading}
            emptyHint={
              isHistorical
                ? historicalUnavailable
                  ? t("logs.historicalUnavailableEmpty")
                  : historicalState.loading
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
          />
          <p className="text-muted-foreground mt-2 text-xs">
            {t("logs.bufferCap", { limit: LOG_BUFFER_LIMIT })}{" "}
            <code className="bg-muted text-2xs rounded px-1 py-0.5 font-mono">
              astro logs --app={a.slug} --follow
            </code>
            .{" "}
            <Link href="/downloads" className="underline">
              {t("logs.installCli")}
            </Link>
            .
          </p>
        </CardContent>
      </Card>

      {/* ─── #422 platform events panel — auto-expands on warnings ─── */}
      <PodEventsPanel appEvents={appEvents} loading={events.loading} />

      {/* ─── #648 alert-rules panel ────────────────────────────────── */}
      <AlertRulesPanel appId={a.id} appName={a.name} />
    </PageShell>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// #648 — Alerts panel
// ───────────────────────────────────────────────────────────────────────────

function AlertRulesPanel({ appId, appName }: { appId: string; appName: string }) {
  const refetchVars = React.useMemo(
    () => ({ target: "app", targetId: appId, activeOnly: false }),
    [appId]
  );

  const rules = useQuery<AlertRulesResp>(LIST_ALERT_RULES, {
    variables: refetchVars,
    fetchPolicy: "cache-and-network",
  });

  const refetchQueries = React.useMemo(
    () => [{ query: LIST_ALERT_RULES, variables: refetchVars }],
    [refetchVars]
  );

  const [createRule, createState] = useMutation<{
    createAlertRule: MutationResult<AlertRule>;
  }>(CREATE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });
  const [deleteRule, deleteState] = useMutation<{
    deleteAlertRule: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });
  const [muteRule, muteState] = useMutation<{
    muteAlertRule: MutationResult<AlertRule>;
  }>(MUTE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });
  const [unmuteRule, unmuteState] = useMutation<{
    unmuteAlertRule: MutationResult<AlertRule>;
  }>(UNMUTE_ALERT_RULE, { refetchQueries, awaitRefetchQueries: true });

  const busy =
    createState.loading || deleteState.loading || muteState.loading || unmuteState.loading;

  const [createOpen, setCreateOpen] = React.useState(false);
  const [muteTarget, setMuteTarget] = React.useState<AlertRule | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AlertRule | null>(null);
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set());

  const ruleList = rules.data?.astroliftAlertRules ?? [];

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleUnmute(r: AlertRule) {
    const { data } = await unmuteRule({ variables: { input: { ruleId: r.id } } });
    if (data?.unmuteAlertRule.ok) {
      toast.success(`Unmuted ${r.name}`);
    } else {
      toast.error(data?.unmuteAlertRule.errors?.[0]?.message ?? "Unmute failed");
    }
  }

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <BellIcon className="size-4" /> Alert rules
            </CardTitle>
            <CardDescription>
              Thresholds that page on-call when {appName} crosses them. Mute to silence without
              losing the definition; delete to retire it.
            </CardDescription>
          </div>
          <Button size="sm" onClick={() => setCreateOpen(true)} disabled={busy}>
            <PlusIcon className="size-3.5" /> Add alert rule
          </Button>
        </CardHeader>
        <CardContent className="p-0">
          {rules.loading && ruleList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : ruleList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BellIcon className="size-5" />}
                title="No alert rules for this app"
                description="Add a rule to page on-call when latency, error rate, or saturation crosses a threshold."
              />
            </div>
          ) : (
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
                                onClick={() => void handleUnmute(r)}
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
                            <AlertEventsList ruleId={r.id} />
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <CreateAlertRuleSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        appId={appId}
        busy={createState.loading}
        onSubmit={async (input) => {
          const { data } = await createRule({ variables: { input } });
          if (data?.createAlertRule.ok) {
            toast.success(`Created ${input.name}`);
            setCreateOpen(false);
            return true;
          }
          toast.error(data?.createAlertRule.errors?.[0]?.message ?? "Create failed");
          return false;
        }}
      />

      <MuteAlertRuleSheet
        key={muteTarget?.id ?? "none"}
        target={muteTarget}
        onOpenChange={(next) => {
          if (!next) setMuteTarget(null);
        }}
        busy={muteState.loading}
        onSubmit={async (durationSeconds, reason) => {
          if (!muteTarget) return;
          const { data } = await muteRule({
            variables: { input: { ruleId: muteTarget.id, durationSeconds, reason } },
          });
          if (data?.muteAlertRule.ok) {
            toast.success(`Muted ${muteTarget.name}`);
            setMuteTarget(null);
          } else {
            toast.error(data?.muteAlertRule.errors?.[0]?.message ?? "Mute failed");
          }
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
          const { data } = await deleteRule({
            variables: { input: { id: deleteTarget.id } },
          });
          if (!data?.deleteAlertRule.ok) {
            throw new Error(data?.deleteAlertRule.errors?.[0]?.message ?? "Delete failed");
          }
          toast.success(`Deleted ${deleteTarget.name}`);
        }}
      />
    </>
  );
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

function AlertEventsList({ ruleId }: { ruleId: string }) {
  const events = useQuery<AlertEventsResp>(LIST_ALERT_EVENTS, {
    variables: { ruleId, unresolvedOnly: false, limit: 5 },
    fetchPolicy: "cache-and-network",
  });

  const [ackEvent, ackState] = useMutation<{
    acknowledgeAlertEvent: MutationResult<AlertEvent>;
  }>(ACKNOWLEDGE_ALERT_EVENT, {
    refetchQueries: [
      { query: LIST_ALERT_EVENTS, variables: { ruleId, unresolvedOnly: false, limit: 5 } },
    ],
    awaitRefetchQueries: true,
  });

  async function handleAck(e: AlertEvent) {
    const { data } = await ackEvent({ variables: { input: { id: e.id } } });
    if (!data?.acknowledgeAlertEvent.ok) {
      toast.error(data?.acknowledgeAlertEvent.errors?.[0]?.message ?? "Ack failed");
    }
  }

  const eventList = events.data?.astroliftAlertEvents ?? [];

  if (events.loading && eventList.length === 0) {
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
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void handleAck(e)}
                    disabled={ackState.loading}
                  >
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
  onSubmit: (input: {
    name: string;
    target: string;
    targetId: string;
    severity: string;
    predicate: Record<string, unknown>;
    notifyChannels: Array<Record<string, unknown>>;
    isActive: boolean;
  }) => Promise<boolean>;
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
