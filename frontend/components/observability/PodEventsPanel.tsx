"use client";

/**
 * PodEventsPanel — the app-scoped slice of the platform event stream
 * shown on the App > Observability page (#422).
 *
 * Two jobs:
 *
 *  1. Render the recent platform events for one app (mirrors the
 *     pre-#422 inline section in ``observability-client.tsx``).
 *  2. Surface warnings zero-click — the card defaults to collapsed,
 *     but auto-expands when the event stream contains one or more
 *     warning-shaped events in the last hour, and paints the header
 *     amber so operators see the problem at a glance.
 *
 * "Warning-shaped" is a pure FE classifier (see :func:`classifyEvent`)
 * — we don't add a backend severity column for v1. The backend's
 * ``event_type`` follows the ``{resource}.{action}`` convention, so we
 * match on a small allow-list of bad-news suffixes plus an explicit
 * ``severity`` / ``failed_reason`` payload field if present.
 */

import {
  ActivityIcon,
  AlertTriangleIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ExternalLinkIcon,
  ScrollTextIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export interface PodEventRow {
  id: string;
  eventType: string;
  payload: Record<string, unknown>;
  occurredAt: string;
}

export interface PodEventsPanelProps {
  appEvents: PodEventRow[];
  loading: boolean;
  /**
   * Window (in ms) over which an event still counts as a "recent"
   * warning. Defaults to one hour. Exposed so tests can pin a
   * deterministic window.
   */
  recentWindowMs?: number;
  /**
   * Hook the wall clock so tests can pin "now". Defaults to
   * :func:`Date.now`.
   */
  nowMs?: number;
}

// 1 hour. Matches the operator-facing copy: "N warnings in the
// last hour".
const DEFAULT_RECENT_WINDOW_MS = 60 * 60 * 1000;

// Bad-news action suffixes that count as warnings. Kept narrow so a
// run-of-the-mill ``deploy.completed`` never reads as warning.
const WARNING_ACTION_SUFFIXES = [
  ".failed",
  ".error",
  ".errored",
  ".crash",
  ".crashed",
  ".crashloop",
  ".unhealthy",
  ".timeout",
  ".timed_out",
  ".degraded",
  ".unreachable",
  ".rollback",
  ".rolled_back",
  ".aborted",
] as const;

// Payload-level severities promoted from third-party sources (alert
// events round-tripped from Zentinelle / synthetic checks / etc.).
const WARNING_SEVERITIES = new Set(["warning", "warn", "error", "critical", "high"]);

/**
 * Classify one event row as a warning or not. Pure — exported for
 * tests + for the panel to count without re-running the same logic.
 */
export function classifyEvent(e: PodEventRow): boolean {
  const t = (e.eventType ?? "").toLowerCase();
  for (const suffix of WARNING_ACTION_SUFFIXES) {
    if (t.endsWith(suffix)) return true;
  }
  // Generic ``*.warning`` family — covers ad-hoc warning emits the
  // bad-news suffix list misses.
  if (t.endsWith(".warning") || t.endsWith(".warn")) return true;

  const payload = e.payload ?? {};
  const severity = String(payload.severity ?? payload.level ?? "")
    .trim()
    .toLowerCase();
  if (severity && WARNING_SEVERITIES.has(severity)) return true;
  if (typeof payload.failed_reason === "string" && payload.failed_reason.trim() !== "") {
    return true;
  }
  if (typeof payload.error === "string" && payload.error.trim() !== "") return true;
  return false;
}

/**
 * Count warning-shaped events that landed inside ``windowMs`` of
 * ``nowMs``. Bad/unparseable timestamps are dropped silently — we
 * never want a clock-skew event to crash the panel.
 */
export function countRecentWarnings(
  events: PodEventRow[],
  nowMs: number,
  windowMs: number
): number {
  let count = 0;
  for (const e of events) {
    if (!classifyEvent(e)) continue;
    const ts = Date.parse(e.occurredAt);
    if (Number.isNaN(ts)) continue;
    if (nowMs - ts <= windowMs) count += 1;
  }
  return count;
}

export function PodEventsPanel({
  appEvents,
  loading,
  recentWindowMs = DEFAULT_RECENT_WINDOW_MS,
  nowMs,
}: PodEventsPanelProps) {
  const t = useTranslations("apps.observability");

  // ``Date.now()`` is impure; calling it during render trips
  // react-hooks/purity. Use a prop-driven effect so the "now"
  // snapshot lands in state on every poll cycle (the parent re-fetches
  // ``LIST_EVENTS`` every 15s and the new events array is a fresh
  // identity). Tests pin ``nowMs`` directly to avoid the effect.
  const [autoNow, setAutoNow] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (nowMs != null) return;
    // Snapshotting wall-clock inside an effect is the React-blessed
    // way to keep render pure while still tracking real time.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAutoNow(Date.now());
  }, [appEvents, nowMs]);
  const effectiveNow = nowMs ?? autoNow ?? 0;

  const warningCount = React.useMemo(
    () => countRecentWarnings(appEvents, effectiveNow, recentWindowMs),
    [appEvents, effectiveNow, recentWindowMs]
  );

  // Auto-open when warnings land; operator can still collapse/expand
  // manually after. We track an *override* so the auto-rule doesn't
  // fight the operator on every poll.
  const [override, setOverride] = React.useState<boolean | null>(null);
  const isOpen = override ?? warningCount > 0;
  const autoExpandedDueToWarnings = override === null && warningCount > 0;

  const headerToneClass = autoExpandedDueToWarnings
    ? "bg-warning/10 border-b border-warning-border"
    : "";

  const Icon = autoExpandedDueToWarnings ? AlertTriangleIcon : ActivityIcon;
  const iconColorClass = autoExpandedDueToWarnings ? "text-warning-fg" : "";

  return (
    <Card>
      <CardHeader
        className={`flex flex-row items-start justify-between gap-3 space-y-0 pb-3 ${headerToneClass}`}
      >
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Icon className={`size-4 ${iconColorClass}`} /> {t("events.title")}
          </CardTitle>
          <CardDescription>
            {warningCount > 0
              ? t("events.warningCount", { count: warningCount })
              : t("events.description")}
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/events"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            {t("events.all")} <ExternalLinkIcon className="size-3" />
          </Link>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2"
            aria-expanded={isOpen}
            aria-controls="pod-events-panel-body"
            onClick={() => setOverride(!isOpen)}
          >
            {isOpen ? (
              <>
                <ChevronUpIcon className="size-3" /> {t("events.collapse")}
              </>
            ) : (
              <>
                <ChevronDownIcon className="size-3" /> {t("events.expand")}
              </>
            )}
          </Button>
        </div>
      </CardHeader>
      {isOpen && (
        <CardContent id="pod-events-panel-body" className="p-0">
          {loading && appEvents.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : appEvents.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ScrollTextIcon className="size-5" />}
                title={t("events.emptyTitle")}
                description={t("events.emptyDescription")}
              />
            </div>
          ) : (
            <ul className="divide-y">
              {appEvents.slice(0, 25).map((e) => {
                const isWarning = classifyEvent(e);
                return (
                  <li key={e.id} className="px-6 py-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-muted-foreground w-44 shrink-0 font-mono text-xs">
                        {new Date(e.occurredAt).toLocaleString()}
                      </span>
                      <Badge
                        variant={isWarning ? "destructive" : "outline"}
                        className="font-mono text-xs"
                      >
                        {e.eventType}
                      </Badge>
                      {isWarning && (
                        <AlertTriangleIcon
                          className="text-warning-fg size-3"
                          aria-label={t("events.warningBadgeLabel")}
                        />
                      )}
                    </div>
                    {Object.keys(e.payload).length > 0 && (
                      <pre className="text-muted-foreground text-2xs mt-1 ml-44 overflow-x-auto font-mono">
                        {JSON.stringify(e.payload, null, 2)}
                      </pre>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      )}
    </Card>
  );
}
