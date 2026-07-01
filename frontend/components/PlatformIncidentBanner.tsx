"use client";

/**
 * #728 — top-of-page banner that surfaces unresolved incidents from
 * the platform's public Statuspage.io feed. The footer link from
 * #702 stays as the always-on "is the platform OK" indicator; this
 * banner only appears when there's an active incident so operators
 * see the news the moment they load any page.
 *
 * The poll cadence (60s) matches the dashboard auto-refresh elsewhere
 * in the app — Statuspage.io rate-limits at one request per second
 * per origin so 60s is comfortably under that ceiling with headroom
 * for many tabs.
 *
 * Dismissal keys include all currently-active incident IDs so a new
 * incident re-shows the banner even if the operator already dismissed
 * a previous one this session.
 */

import { AlertTriangleIcon, ExternalLinkIcon, XIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

interface StatusIncident {
  id: string;
  name: string;
  status: string;
  impact: "none" | "minor" | "major" | "critical";
  shortlink: string;
  resolved_at: string | null;
}

interface StatusSummary {
  incidents: StatusIncident[];
}

const POLL_INTERVAL_MS = 60_000;
const DISMISS_PREFIX = "astrolift:incident-dismissed:";

export function PlatformIncidentBanner() {
  const statusPageUrl = process.env.NEXT_PUBLIC_STATUS_PAGE_URL;
  const [incidents, setIncidents] = React.useState<StatusIncident[]>([]);
  // Dismissed keys are a per-session set. Initialised lazily from
  // sessionStorage so SSR returns an empty set and the client hydrates
  // any previously-acknowledged incident IDs on first render.
  const [dismissed, setDismissed] = React.useState<Set<string>>(() => new Set());
  const dismissedHydrated = React.useRef(false);

  React.useEffect(() => {
    if (dismissedHydrated.current) return;
    dismissedHydrated.current = true;
    try {
      const next = new Set<string>();
      for (let i = 0; i < window.sessionStorage.length; i += 1) {
        const k = window.sessionStorage.key(i);
        if (k && k.startsWith(DISMISS_PREFIX)) {
          next.add(k.slice(DISMISS_PREFIX.length));
        }
      }
      if (next.size > 0) setDismissed(next);
    } catch {
      // sessionStorage may be unavailable (incognito, quota) — degrade
      // by treating as not-dismissed so the operator still sees the banner.
    }
  }, []);

  React.useEffect(() => {
    if (!statusPageUrl) return;

    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`${statusPageUrl}/api/v2/summary.json`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as StatusSummary;
        if (cancelled) return;
        const active = (data.incidents ?? []).filter(
          (i) => i.resolved_at === null && i.status !== "resolved"
        );
        setIncidents(active);
      } catch {
        // Network error or unreachable status page — keep the UI quiet.
        // Operators get no false-positive "platform incident" banner just
        // because the public status page itself is down.
      }
    }

    poll();
    const id = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [statusPageUrl]);

  const incidentKey = React.useMemo(
    () =>
      incidents
        .map((i) => i.id)
        .sort()
        .join(","),
    [incidents]
  );

  if (!statusPageUrl) return null;
  if (incidents.length === 0) return null;
  if (incidentKey && dismissed.has(incidentKey)) return null;

  const severity = highestImpact(incidents);
  const tone =
    severity === "major" || severity === "critical"
      ? "border-danger-border bg-danger/10 text-danger-fg"
      : "border-warning-border bg-warning/10 text-warning-fg";

  const headline =
    incidents.length === 1 ? incidents[0]!.name : `${incidents.length} active platform incidents`;

  function dismiss() {
    try {
      window.sessionStorage.setItem(`${DISMISS_PREFIX}${incidentKey}`, "1");
    } catch {
      // ignore — dismissal will simply not persist this session
    }
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(incidentKey);
      return next;
    });
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("flex items-center gap-3 border-b px-4 py-2 text-sm", tone)}
    >
      <AlertTriangleIcon className="size-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 truncate font-medium">{headline}</span>
      <a
        href={statusPageUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex shrink-0 items-center gap-1 underline-offset-2 hover:underline"
      >
        View status
        <ExternalLinkIcon className="size-3" aria-hidden />
      </a>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss incident banner"
        className="inline-flex shrink-0 items-center justify-center rounded p-1 hover:bg-black/5 dark:hover:bg-white/10"
      >
        <XIcon className="size-4" aria-hidden />
      </button>
    </div>
  );
}

function highestImpact(incidents: StatusIncident[]): StatusIncident["impact"] {
  const order: StatusIncident["impact"][] = ["none", "minor", "major", "critical"];
  let best: StatusIncident["impact"] = "none";
  for (const i of incidents) {
    if (order.indexOf(i.impact) > order.indexOf(best)) best = i.impact;
  }
  return best;
}
