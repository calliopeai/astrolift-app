import { useTranslations } from "next-intl";
import * as React from "react";

import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { StatusBadge, type StatusTone } from "@calliopelabs/ui";

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
 * Colours come from the one status map (@calliopelabs/ui status tones), shared with
 * ``StatusDot``:
 *   running  → success (never the accent)
 *   failed   → red
 *   pending* → amber (operator attention required)
 *   deploying / redeploying → info green + pulse (in-flight)
 *   superseded / rolled_back → muted (terminal, no-op)
 */

// Labels come from `apps.deployments.statuses`, keyed by the status itself.
const STATUS_TONE: Record<DeploymentStatus, StatusTone> = {
  pending_approval: "warn",
  pending: "warn",
  deploying: "info",
  redeploying: "info",
  running: "ok",
  failed: "error",
  rolled_back: "muted",
  superseded: "muted",
};

const IN_FLIGHT_STATUSES = new Set<DeploymentStatus>(["deploying", "redeploying"]);

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
    <StatusBadge
      tone={STATUS_TONE[status]}
      pulse={IN_FLIGHT_STATUSES.has(status)}
      struck={status === "superseded"}
      className={className}
      aria-label={pill("label", { status: text })}
    >
      {text}
    </StatusBadge>
  );
}
