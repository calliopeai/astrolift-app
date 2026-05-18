"use client";

import { useLazyQuery, useQuery, useSubscription } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  HistoryIcon,
  PauseIcon,
  PlayIcon,
  TerminalIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import {
  DnsRecordsCard,
  GoldenSignalsPanel,
  LogViewer,
  ManagedServiceMetricsList,
  MetricScopePicker,
  PodEventsPanel,
  TlsCertificatesCard,
  WorkloadIdentityCard,
} from "@/components/observability";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import { ON_APP_LOG, ON_APP_LOGS } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftAppLogLine, AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP_LOGS } from "@/graphql/observability/observability.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

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
// only" empty state in that case.
interface HistoricalLogsResp {
  astroliftAppLogs: {
    items: AstroliftAppLogLine[];
    nextCursor: string;
    reachedRetention: boolean;
    historicalAvailable: boolean;
    totalCount: number;
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

export function ObservabilityClient({ slug }: { slug: string }) {
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
  const [historicalRange, setHistoricalRange] =
    React.useState<HistoricalRangeValue>("live");
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
  const [fetchHistorical, historicalState] = useLazyQuery<HistoricalLogsResp>(
    GET_APP_LOGS,
    { fetchPolicy: "no-cache" }
  );

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
                actionHref={`/apps/${a.slug}/deployments`}
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
                  return (
                    <TableRow
                      key={pod.name}
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
                      <TableCell className="text-right font-mono text-xs">{pod.restarts}</TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {formatAge(pod.age)}
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {pod.node || "—"}
                      </TableCell>
                    </TableRow>
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
          appSlug={a.slug}
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
        />
      </div>
      <GoldenSignalsPanel
        appSlug={a.slug}
        environmentName={scopedEnv}
        workloadSlug={scopedWorkload}
      />

      {/* ─── #645 / #646 managed-service metric tiles ─────────────── */}
      <ManagedServiceMetricsList
        managedServices={managedServices.data?.astroliftManagedServices ?? []}
      />

      {/* ─── #377 observability cards (DNS / TLS / Workload identity) ── */}
      <DnsRecordsCard appSlug={a.slug} />
      <TlsCertificatesCard appSlug={a.slug} />
      <WorkloadIdentityCard appSlug={a.slug} />

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
                  <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
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
                path; any other value swaps to the paginated query. */}
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
            <div className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 mb-3 flex items-start gap-2 rounded-md border p-2 text-xs">
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
            <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
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
    </PageShell>
  );
}
