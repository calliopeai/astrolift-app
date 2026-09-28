"use client";

/**
 * Unresolved incidents from the platform's public Statuspage.io feed (#728),
 * polled every 60s, minus the set this session dismissed. The data half of
 * PlatformIncidentBanner (Storybook first: the component is pure).
 *
 * Dismissal keys include all currently-active incident IDs so a new incident
 * re-shows the banner even if the operator already dismissed a previous one.
 */

import * as React from "react";

import type { StatusIncident } from "@/components/PlatformIncidentBanner";

interface StatusSummary {
  incidents: StatusIncident[];
}

const POLL_INTERVAL_MS = 60_000;
const DISMISS_PREFIX = "astrolift:incident-dismissed:";

export function usePlatformIncidents(statusPageUrl: string | undefined): {
  incidents: StatusIncident[];
  dismiss: () => void;
} {
  const [incidents, setIncidents] = React.useState<StatusIncident[]>([]);
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

  const visible = incidentKey && dismissed.has(incidentKey) ? [] : incidents;
  return { incidents: visible, dismiss };
}
