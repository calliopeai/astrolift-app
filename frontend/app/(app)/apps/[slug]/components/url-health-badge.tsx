"use client";

import { useQuery } from "@apollo/client/react";
import { Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  GET_APP_URL_HEALTH,
  GET_APP_URL_PROBE_HISTORY,
} from "@/graphql/observability/observability.queries";
import { cn } from "@/lib/utils";

// Match the wire enum on `AstroliftAppUrlHealth.status`. Kept narrow
// here (rather than imported from the codegen output) so this
// component compiles against any frontend that has had codegen run
// at least once — useful in worktrees where codegen hasn't been
// pulled fresh.
type UrlHealthStatus = "ok" | "degraded" | "down" | "unknown";

interface UrlHealthRow {
  url: string;
  status: UrlHealthStatus | string;
  statusCode: number | null;
  latencyMs: number | null;
  lastChecked: string;
  message: string;
}

interface HealthResp {
  astroliftAppUrlHealth: UrlHealthRow | null;
}

interface HistoryResp {
  astroliftAppUrlProbeHistory: UrlHealthRow[];
}

// 30s matches the backend cache TTL. A poll faster than this just
// reads the cached payload server-side; slower than this and we
// leave operators staring at stale pills.
const POLL_INTERVAL_MS = 30_000;

// Treat anything older than this as worth re-fetching even on first
// mount. The cache key on the server is per (app, url), so two
// operators on the same overview share the work.

interface Props {
  appSlug: string;
  url: string;
  /** Compact mode renders just the dot + latency, no status code. */
  compact?: boolean;
  className?: string;
}

/**
 * Live HTTP health pill for an app URL (#406).
 *
 * Polls `astroliftAppUrlHealth` every 30s while the page is visible
 * (suspends polling when the tab is hidden — no point burning probes
 * for an off-screen overview). Clicking the pill issues an immediate
 * re-probe with `forceRefresh: true`. Hovering opens a tooltip with
 * the last-5 probe history + uptime ratio for the window.
 */
export function UrlHealthBadge({ appSlug, url, compact, className }: Props) {
  const t = useTranslations("apps.detail.urlHealth");
  const tt = useTranslations("apps.detail.urlHealth.tooltip");
  const [isVisible, setIsVisible] = React.useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden"
  );
  const [historyOpen, setHistoryOpen] = React.useState(false);

  // Suspend polling when the tab is hidden so we don't keep hitting
  // the operator's apps from a backgrounded tab. The browser fires
  // visibilitychange synchronously when the tab gains/loses focus —
  // we flip pollInterval to 0 in the off state, which Apollo treats
  // as "no polling".
  React.useEffect(() => {
    if (typeof document === "undefined") return;
    const onChange = () => setIsVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  const { data, loading, refetch } = useQuery<HealthResp>(GET_APP_URL_HEALTH, {
    variables: { appSlug, url, forceRefresh: false },
    fetchPolicy: "cache-and-network",
    pollInterval: isVisible ? POLL_INTERVAL_MS : 0,
    notifyOnNetworkStatusChange: true,
  });

  // History query — fetched lazily on tooltip open (Apollo won't
  // issue a network round-trip until the query mounts because we
  // pass `skip` based on the tooltip's open state).
  const historyQ = useQuery<HistoryResp>(GET_APP_URL_PROBE_HISTORY, {
    variables: { appSlug, url, limit: 5 },
    fetchPolicy: "cache-and-network",
    skip: !historyOpen,
  });

  const health = data?.astroliftAppUrlHealth;
  const status = (health?.status as UrlHealthStatus | undefined) ?? "unknown";
  // `loading` flips true on both first-mount and pollInterval ticks;
  // we only want the spinner on the first-ever load (no data yet).
  const isInitialLoading = loading && !health;

  async function handleClick() {
    // forceRefresh tells the backend to skip its 30s result cache so
    // the operator immediately sees a fresh probe.
    await refetch({ appSlug, url, forceRefresh: true });
  }

  const ariaLabel = t("ariaLabel", { status: isInitialLoading ? t("checking") : status });

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip onOpenChange={setHistoryOpen}>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={handleClick}
            aria-label={ariaLabel}
            className={cn(
              "focus-visible:ring-ring/50 inline-flex items-center gap-1.5",
              "rounded-full border px-2 py-0.5 text-xs font-medium transition-colors",
              "outline-none focus-visible:ring-3",
              statusContainerClass(status, isInitialLoading),
              className
            )}
          >
            <span aria-hidden className="flex items-center">
              {isInitialLoading ? (
                <Loader2Icon className="size-3 animate-spin" />
              ) : (
                <span
                  className={cn(
                    "inline-block size-2 rounded-full",
                    statusDotClass(status)
                  )}
                />
              )}
            </span>
            <span className="font-mono">
              {renderLabel({
                status,
                isInitialLoading,
                health,
                t,
                compact,
              })}
            </span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" sideOffset={6} className="max-w-sm">
          <HistoryTooltipBody
            health={health}
            history={historyQ.data?.astroliftAppUrlProbeHistory ?? []}
            loadingHistory={historyQ.loading}
            rechecking={loading && !!health}
            t={tt}
          />
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ─── view helpers ─────────────────────────────────────────────────────────────

function statusContainerClass(status: UrlHealthStatus | string, loading: boolean): string {
  if (loading) return "border-border bg-muted text-muted-foreground";
  switch (status) {
    case "ok":
      return "border-success-border bg-success/10 text-success-fg hover:bg-success/20";
    case "degraded":
      return "border-warning-border bg-warning/10 text-warning-fg hover:bg-warning/20";
    case "down":
      return "border-danger-border bg-danger/10 text-danger-fg hover:bg-danger/20";
    case "unknown":
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

function statusDotClass(status: UrlHealthStatus | string): string {
  switch (status) {
    case "ok":
      return "bg-success";
    case "degraded":
      return "bg-warning";
    case "down":
      return "bg-danger";
    default:
      return "bg-muted-foreground";
  }
}

function renderLabel({
  status,
  isInitialLoading,
  health,
  t,
  compact,
}: {
  status: UrlHealthStatus | string;
  isInitialLoading: boolean;
  health: UrlHealthRow | null | undefined;
  t: ReturnType<typeof useTranslations>;
  compact: boolean | undefined;
}): string {
  if (isInitialLoading) return t("checking");
  if (!health || status === "unknown") return t("unknown");

  const latency = health.latencyMs ?? 0;
  if (status === "ok") return t("ok", { latency });
  if (status === "degraded") {
    if (compact) return t("ok", { latency });
    return t("degraded", {
      statusCode: health.statusCode ?? "—",
      latency,
    });
  }
  // down: show the reason if we have one, else the generic label.
  const reason = (health.message || "").trim();
  return reason ? t("down", { reason }) : t("downNoReason");
}

// ─── history tooltip body ─────────────────────────────────────────────────────

function HistoryTooltipBody({
  health,
  history,
  loadingHistory,
  rechecking,
  t,
}: {
  health: UrlHealthRow | null | undefined;
  history: UrlHealthRow[];
  loadingHistory: boolean;
  rechecking: boolean;
  t: ReturnType<typeof useTranslations>;
}) {
  // Prefer the dedicated history list; fall back to the current
  // result so the tooltip always shows something on first hover.
  const rows = history.length > 0 ? history : health ? [health] : [];

  const okCount = rows.filter((r) => r.status === "ok").length;
  const avg =
    rows.length > 0
      ? Math.round(
          rows.reduce((acc, r) => acc + (r.latencyMs ?? 0), 0) / rows.length
        )
      : 0;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold">{t("title")}</span>
        {rechecking ? (
          <span className="text-background/70 inline-flex items-center gap-1 text-2xs">
            <Loader2Icon className="size-3 animate-spin" /> {t("rechecking")}
          </span>
        ) : (
          <span className="text-background/70 text-2xs">{t("recheck")}</span>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="text-background/70 text-2xs">
          {loadingHistory ? t("rechecking") : t("empty")}
        </p>
      ) : (
        <>
          <p className="text-background/80 font-mono text-2xs">
            {t("uptime", { ok: okCount, total: rows.length, avgMs: avg })}
          </p>
          <ul className="space-y-1">
            {rows.map((r, i) => (
              <li
                key={`${r.lastChecked}-${i}`}
                className="flex items-center justify-between gap-3 font-mono text-2xs"
              >
                <span className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className={cn("inline-block size-1.5 rounded-full", statusDotClass(r.status))}
                  />
                  <span>{r.statusCode ?? "—"}</span>
                  <span className="text-background/60">{(r.latencyMs ?? 0)}ms</span>
                </span>
                <span className="text-background/60">
                  {relativeShort(r.lastChecked, t)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function relativeShort(iso: string, t: ReturnType<typeof useTranslations>): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const delta = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (delta < 5) return t("now");
  if (delta < 60) return t("secondsAgo", { seconds: delta });
  if (delta < 3600) return t("minutesAgo", { minutes: Math.floor(delta / 60) });
  return t("hoursAgo", { hours: Math.floor(delta / 3600) });
}
