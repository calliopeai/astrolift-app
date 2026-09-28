"use client";

import { useLazyQuery, useQuery, useSubscription } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { ObservabilityPanelReason } from "@/components/observability/panel-reason";
import { useMetricScopeOptions } from "@/components/observability/use-metric-scope-options";
import { usePodResourceUsage } from "@/components/observability/use-pod-resource-usage";
import {
  downloadTextFile,
  formatLogFile,
  logFilename,
  toLogLines,
} from "@/components/screens/apps/deployments/app-log-lines";
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import { ON_APP_LOG, ON_APP_LOGS } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftAppLogLine, AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP_LOGS } from "@/graphql/observability/observability.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";

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
export const HISTORICAL_RANGES = [
  { value: "live", seconds: 0 },
  { value: "15m", seconds: 15 * 60 },
  { value: "1h", seconds: 60 * 60 },
  { value: "6h", seconds: 6 * 60 * 60 },
  { value: "24h", seconds: 24 * 60 * 60 },
] as const;

export type HistoricalRangeValue = (typeof HISTORICAL_RANGES)[number]["value"];

// Live cluster surface — pods refetch periodically as a safety net
// against missed subscription events (the deploy.lifecycle stream
// is the canonical 'something changed' signal but the runtime
// cluster itself doesn't push us pod-state events).
export const POD_POLL_MS = 5000;
export const LOG_BUFFER_LIMIT = 500;
const DEFAULT_TAIL_LINES = 200;

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

/**
 * App › Observability data: the app, its pods (polled), platform events,
 * the env/workload scope (URL-backed), the pod/container pick, and the log
 * pane in its three modes (per-pod live tail, all-replicas live tail,
 * historical window). The metric panels beside it have their own hooks.
 */
export function useAppObservability(slug: string) {
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

  const a = app.data?.astroliftApp ?? null;
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
  const podUsage = usePodResourceUsage(slug, selectedPod, scopedEnv);

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

  const tViewer = useTranslations("apps.logViewer");
  function downloadLogs() {
    if (logBuffer.length === 0) {
      toast.info(tViewer("downloadEmpty"));
      return;
    }
    const many = allReplicas || isHistorical;
    const filename = logFilename([
      { value: a?.slug ?? slug, fallback: "app" },
      { value: scopedEnv, fallback: "all" },
      { value: many ? null : selectedPod, fallback: "pod" },
    ]);
    downloadTextFile(filename, formatLogFile(toLogLines(logBuffer, { withPodName: many })));
    toast.success(tViewer("downloadStarted", { filename }));
  }

  return {
    slug,
    app: a,
    loading: app.loading && !a,
    // Pods
    pods: podRows,
    podsLoading: pods.loading && podRows.length === 0,
    selectedPod,
    onPickPod: setPickedPod,
    podContainers,
    selectedContainer,
    onPickContainer: setPickedContainer,
    podUsage,
    // Metric scope (URL-backed)
    scopedEnv,
    scopedWorkload,
    onEnvChange: handleEnvChange,
    onWorkloadChange: handleWorkloadChange,
    scopeOptions,
    managedServices: managedServices.data?.astroliftManagedServices ?? [],
    // Logs
    logBuffer,
    onClearLogs: () => setLogBuffer([]),
    /** Saves the whole buffer, whatever the screen's filters show. */
    onDownloadLogs: downloadLogs,
    streaming,
    onToggleStreaming: () => setStreaming((s) => !s),
    allReplicas,
    onToggleAllReplicas: () => setAllReplicas((v) => !v),
    historicalRange,
    onHistoricalRangeChange: setHistoricalRange,
    isHistorical,
    historicalUnavailable,
    historicalLoading: historicalState.loading,
    onRefreshHistorical: () => runHistoricalQuery(historicalRange),
    // Platform events
    appEvents,
    eventsLoading: events.loading,
  };
}
