import * as React from "react";

import { Badge } from "@/components/ui/badge";
import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { IN_FLIGHT, PILL_TONE } from "@/lib/status-tones";
import { cn } from "@/lib/utils";

/**
 * Shared status pill for deployment rows (#411 scope B).
 *
 * Replaces the inline ``<Badge variant="secondary"
 * className="capitalize">{d.status.replace(/_/g, " ")}</Badge>`` reads
 * scattered across the global queue, the per-app deployments tab, the
 * deployment-detail page, and the approval-detail page. One component
 * means every surface picks up the same colour tokens, the same
 * animated pulse on in-flight states, and the same a11y label.
 *
 * Colours come from the one status map (lib/status-tones), shared with
 * ``StatusDot``:
 *   running  → success (never the accent)
 *   failed   → red
 *   pending* → amber (operator attention required)
 *   deploying / redeploying → info green + pulse (in-flight)
 *   superseded / rolled_back → muted (terminal, no-op)
 */

interface PillStyle {
  className: string;
  label: string;
}

const STATUS_STYLES: Record<DeploymentStatus, PillStyle> = {
  pending_approval: { className: PILL_TONE.warn, label: "Pending approval" },
  pending: { className: PILL_TONE.warn, label: "Pending" },
  deploying: { className: cn(PILL_TONE.info, IN_FLIGHT), label: "Deploying" },
  redeploying: { className: cn(PILL_TONE.info, IN_FLIGHT), label: "Redeploying" },
  running: { className: PILL_TONE.ok, label: "Running" },
  failed: { className: PILL_TONE.error, label: "Failed" },
  rolled_back: { className: PILL_TONE.muted, label: "Rolled back" },
  superseded: { className: cn(PILL_TONE.muted, "line-through"), label: "Superseded" },
};

interface DeploymentStatusPillProps {
  status: DeploymentStatus;
  /** Override the human-readable label (e.g. a translated string). */
  label?: string;
  className?: string;
}

export function DeploymentStatusPill({ status, label, className }: DeploymentStatusPillProps) {
  const style = STATUS_STYLES[status];
  return (
    <Badge
      variant="outline"
      className={cn("font-mono font-semibold", style.className, className)}
      aria-label={`Status: ${label ?? style.label}`}
    >
      <span
        aria-hidden
        className="size-1.5 rounded-full bg-current shadow-[0_0_8px_currentColor]"
      />
      {label ?? style.label}
    </Badge>
  );
}
