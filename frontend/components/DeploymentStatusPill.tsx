import { useTranslations } from "next-intl";
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

// Labels come from `apps.deployments.statuses`, keyed by the status itself.
const STATUS_STYLES: Record<DeploymentStatus, string> = {
  pending_approval: PILL_TONE.warn,
  pending: PILL_TONE.warn,
  deploying: cn(PILL_TONE.info, IN_FLIGHT),
  redeploying: cn(PILL_TONE.info, IN_FLIGHT),
  running: PILL_TONE.ok,
  failed: PILL_TONE.error,
  rolled_back: PILL_TONE.muted,
  superseded: cn(PILL_TONE.muted, "line-through"),
};

interface DeploymentStatusPillProps {
  status: DeploymentStatus;
  /** Override the human-readable label (e.g. a translated string). */
  label?: string;
  className?: string;
}

export function DeploymentStatusPill({ status, label, className }: DeploymentStatusPillProps) {
  const t = useTranslations("apps.deployments.statuses");
  const pill = useTranslations("deploymentStatusPill");
  const text = label ?? t(status);
  return (
    <Badge
      variant="outline"
      className={cn("font-mono font-semibold", STATUS_STYLES[status], className)}
      aria-label={pill("label", { status: text })}
    >
      <span
        aria-hidden
        className="size-1.5 rounded-full bg-current shadow-[0_0_8px_currentColor]"
      />
      {text}
    </Badge>
  );
}
