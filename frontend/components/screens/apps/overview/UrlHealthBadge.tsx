"use client";

import { Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import type { UrlHealthRow, UrlHealthStatus, useUrlHealth } from "./use-url-health";

export type UrlHealthBadgeViewProps = ReturnType<typeof useUrlHealth> & {
  /** Compact mode renders just the dot + latency, no status code. */
  compact?: boolean;
  className?: string;
};

/**
 * Live HTTP health pill for an app URL (#406).
 *
 * Clicking the pill issues an immediate re-probe. Hovering opens a
 * tooltip with the last-5 probe history + uptime ratio for the window.
 * The data half (polling, history, re-probe) is useUrlHealth.
 */
export function UrlHealthBadgeView({
  health,
  isInitialLoading,
  rechecking,
  history,
  loadingHistory,
  onHistoryOpenChange,
  onRecheck,
  compact,
  className,
}: UrlHealthBadgeViewProps) {
  const t = useTranslations("apps.detail.urlHealth");
  const tt = useTranslations("apps.detail.urlHealth.tooltip");

  const status = (health?.status as UrlHealthStatus | undefined) ?? "unknown";

  const ariaLabel = t("ariaLabel", { status: isInitialLoading ? t("checking") : status });

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip onOpenChange={onHistoryOpenChange}>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onRecheck}
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
                <span className={cn("inline-block size-2 rounded-full", statusDotClass(status))} />
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
            history={history}
            loadingHistory={loadingHistory}
            rechecking={rechecking}
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
      ? Math.round(rows.reduce((acc, r) => acc + (r.latencyMs ?? 0), 0) / rows.length)
      : 0;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold">{t("title")}</span>
        {rechecking ? (
          <span className="text-background/70 text-2xs inline-flex items-center gap-1">
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
          <p className="text-background/80 text-2xs font-mono">
            {t("uptime", { ok: okCount, total: rows.length, avgMs: avg })}
          </p>
          <ul className="space-y-1">
            {rows.map((r, i) => (
              <li
                key={`${r.lastChecked}-${i}`}
                className="text-2xs flex items-center justify-between gap-3 font-mono"
              >
                <span className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className={cn("inline-block size-1.5 rounded-full", statusDotClass(r.status))}
                  />
                  <span>{r.statusCode ?? "—"}</span>
                  <span className="text-background/60">{r.latencyMs ?? 0}ms</span>
                </span>
                <span className="text-background/60">{relativeShort(r.lastChecked, t)}</span>
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
