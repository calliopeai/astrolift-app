"use client";

import { useTranslations } from "next-intl";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { useRelativeAge } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  type BrainSummary,
  freshnessOf,
  healthOf,
  INGEST_TONE,
  shortSha,
} from "./brain-status-model";

export type BrainStatusPart = "health" | "rebuilt" | "freshness";

// The row's link is an ::after overlay across the row; anything with a
// tooltip has to sit above it to be hovered.
const ABOVE_ROW_LINK = "relative z-10";

/**
 * The list's status columns for one semantic layer (#2427): Health (the
 * environment's latest deployment), Last rebuild (when it started, at which
 * commit) and Freshness (compiled when, at the branch head or behind, the
 * last ingest). Pure: the row's cached summary comes in (#2444), so no row
 * reads the source host; freshness is unknown until a status read has run.
 */
export function BrainStatusCell({
  part,
  summary,
}: {
  part: BrainStatusPart;
  /** Null when the layer has no environment yet. */
  summary: BrainSummary | null;
}) {
  const t = useTranslations("brains.status");
  const overview = useTranslations("brains.overview");
  const age = useRelativeAge();
  const status = summary;
  if (part === "health") return <Health status={status} />;
  if (!status)
    return part === "rebuilt" ? (
      <span className="text-muted-foreground text-xs">{t("never")}</span>
    ) : (
      <span className="text-muted-foreground text-xs">{t("unknown")}</span>
    );
  if (part === "rebuilt") {
    const deployment = status.deployment;
    if (!deployment?.createdAt)
      return <span className="text-muted-foreground text-xs">{t("never")}</span>;
    return (
      <span className="block min-w-0">
        <span className={cn("block text-xs", ABOVE_ROW_LINK)} title={deployment.createdAt}>
          {age(deployment.createdAt)}
        </span>
        {deployment.commitSha && (
          <span className="text-muted-foreground block font-mono text-xs">
            {shortSha(deployment.commitSha)}
          </span>
        )}
      </span>
    );
  }
  if (!status.checkedAt)
    return (
      <span className={cn("text-muted-foreground text-xs", ABOVE_ROW_LINK)} title={t("notChecked")}>
        {t("unknown")}
      </span>
    );
  const fresh = freshnessOf(status);
  return (
    <span className="flex min-w-0 flex-col gap-1">
      {fresh.compiledAt ? (
        <span className={cn("text-xs", ABOVE_ROW_LINK)} title={fresh.compiledAt}>
          {t("compiled", { age: age(fresh.compiledAt) })}
        </span>
      ) : status.deployment?.status === "running" ? (
        <span className="text-warning-fg text-xs">{t("notCompiled")}</span>
      ) : (
        // The compile is read at the running commit: with nothing running
        // there is nothing to read, which is not the same as uncompiled.
        <span className="text-muted-foreground text-xs">{overview("nothingRunning")}</span>
      )}
      <span className="flex flex-wrap items-center gap-1">
        {fresh.behind === true && (
          <Badge variant="outline" className="border-warning-border bg-warning/10 text-warning-fg">
            {t("behind")}
          </Badge>
        )}
        {fresh.ingest && fresh.ingest.state !== "succeeded" && (
          <span className="inline-flex items-center gap-1 text-xs">
            <StatusDot
              status={
                fresh.ingest.state === "running"
                  ? "pending"
                  : INGEST_TONE[fresh.ingest.state] === "error"
                    ? "error"
                    : "muted"
              }
            />
            {t(`ingest.${fresh.ingest.state}`)}
          </span>
        )}
      </span>
    </span>
  );
}

function Health({ status }: { status: BrainSummary | null }) {
  const t = useTranslations("brains.status");
  const health = healthOf(status);
  return (
    <span className="flex min-w-0 flex-col items-start gap-1">
      {health.kind === "deployment" ? (
        <DeploymentStatusPill status={health.status} />
      ) : health.kind === "other" ? (
        <Badge variant="outline" className="text-muted-foreground font-mono">
          {health.status}
        </Badge>
      ) : (
        <span className="text-muted-foreground text-xs">
          {health.kind === "noEnvironment"
            ? t("noEnvironment", { environment: "production" })
            : t("notDeployed")}
        </span>
      )}
      {status && (
        <span className="text-muted-foreground font-mono text-xs">{status.environmentName}</span>
      )}
    </span>
  );
}
