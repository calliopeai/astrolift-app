"use client";

/**
 * #728: top-of-page banner for unresolved platform incidents. Pure (Storybook
 * first): the incidents come from usePlatformIncidents, wired in the app
 * shell. Renders nothing when there is no status page or no incident.
 */

import { AlertTriangleIcon, ExternalLinkIcon, XIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

export interface StatusIncident {
  id: string;
  name: string;
  status: string;
  impact: "none" | "minor" | "major" | "critical";
  shortlink: string;
  resolved_at: string | null;
}

export function PlatformIncidentBanner({
  statusPageUrl,
  incidents,
  onDismiss,
}: {
  statusPageUrl: string | undefined;
  incidents: StatusIncident[];
  onDismiss: () => void;
}) {
  if (!statusPageUrl) return null;
  if (incidents.length === 0) return null;

  const severity = highestImpact(incidents);
  const tone =
    severity === "major" || severity === "critical"
      ? "border-danger-border bg-danger/10 text-danger-fg"
      : "border-warning-border bg-warning/10 text-warning-fg";

  const headline =
    incidents.length === 1 ? incidents[0]!.name : `${incidents.length} active platform incidents`;

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
        onClick={onDismiss}
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
