import * as React from "react";

import { Badge } from "@/components/ui/badge";
import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
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
 * Colour tokens deliberately match the ``StatusDot`` rules:
 *   running  → brand teal (success)
 *   failed   → red
 *   pending* → amber (operator attention required)
 *   deploying / redeploying → blue + pulse (in-flight)
 *   superseded / rolled_back → zinc (terminal, no-op)
 */

interface PillStyle {
  className: string;
  label: string;
}

const STATUS_STYLES: Record<DeploymentStatus, PillStyle> = {
  pending_approval: {
    className: "bg-warning/15 text-warning-fg border-warning-border",
    label: "Pending approval",
  },
  pending: {
    className: "bg-warning/15 text-warning-fg border-warning-border",
    label: "Pending",
  },
  deploying: {
    className: "bg-info/15 text-info-fg border-info-border animate-pulse",
    label: "Deploying",
  },
  redeploying: {
    className: "bg-info/15 text-info-fg border-info-border animate-pulse",
    label: "Redeploying",
  },
  running: {
    className:
      "bg-[color:var(--brand-primary)]/15 text-[color:var(--brand-primary)] border-[color:var(--brand-primary)]/30",
    label: "Running",
  },
  failed: {
    className: "bg-danger/15 text-danger-fg border-danger-border",
    label: "Failed",
  },
  rolled_back: {
    className: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400 border-zinc-500/30",
    label: "Rolled back",
  },
  superseded: {
    className: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400 border-zinc-500/30 line-through",
    label: "Superseded",
  },
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
      className={cn("font-medium", style.className, className)}
      aria-label={`Status: ${label ?? style.label}`}
    >
      {label ?? style.label}
    </Badge>
  );
}
