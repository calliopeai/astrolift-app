"use client";

/**
 * Per-row deployment freshness rollup for the apps list (#405).
 *
 * Renders three things on each app row, side by side:
 *   1. A coloured health-pulse dot (green / amber / red / grey) with
 *      a tooltip explaining the state ("latest deploy failed 5m ago").
 *   2. A "Last deployed: 5m ago" relative-time chip — the absolute
 *      timestamp surfaces in the tooltip on hover.
 *   3. A "View failed deploy" deep-link chip when the latest deploy
 *      is in the ``failed`` status, pointing at the deployment-detail
 *      page so operators can triage in one click.
 *
 * Backend gates the underlying data with the ``includeFreshness``
 * arg on ``astroliftApps`` (off by default to keep the cheap list
 * cheap). This component renders nothing when the pulse field is
 * null — that's the "freshness wasn't requested" signal.
 */

import { AlertOctagonIcon, ClockIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type {
  AppHealthPulseStatus,
  AstroliftAppDeploymentSummary,
  AstroliftAppHealthPulse,
} from "@/graphql/registry/registry.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

interface Props {
  pulse: AstroliftAppHealthPulse | null;
  latestDeployment: AstroliftAppDeploymentSummary | null;
  lastDeployedAt: string | null;
}

const PULSE_BG: Record<AppHealthPulseStatus, string> = {
  OK: "bg-[var(--brand-primary)]",
  DEGRADED: "bg-danger animate-pulse",
  STALE: "bg-warning",
  NEVER: "bg-zinc-400",
};

export function AppFreshnessRow({ pulse, latestDeployment, lastDeployedAt }: Props) {
  const t = useTranslations("apps.list.freshness");
  const fmt = useFormatters();

  // No pulse means the parent list query didn't request the freshness
  // payload (or the backend was wired without it). Render nothing
  // rather than guessing — the absence of a pulse IS the signal.
  if (!pulse) {
    return null;
  }

  const ageBadge = (() => {
    const ts = lastDeployedAt ?? latestDeployment?.createdAt ?? null;
    if (!ts) return null;
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
            <ClockIcon className="size-3" />
            {t("lastDeployed", { when: fmt.formatRelativeTime(ts) })}
          </span>
        </TooltipTrigger>
        <TooltipContent>{fmt.formatDateTime(ts)}</TooltipContent>
      </Tooltip>
    );
  })();

  const failedLink =
    pulse.status === "DEGRADED" && latestDeployment ? (
      <Link
        href={`/deployments/${latestDeployment.id}`}
        onClick={(e) => e.stopPropagation()}
        className="border-destructive/40 text-destructive hover:bg-destructive/15 focus-visible:ring-ring inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none"
      >
        <AlertOctagonIcon className="size-3" />
        {t("viewFailed")}
      </Link>
    ) : null;

  const staleChip =
    pulse.status === "STALE" ? (
      <span className="inline-flex items-center gap-1 rounded-full border border-warning-border bg-warning/15 px-2 py-0.5 text-xs font-medium text-warning-fg">
        {t("stale")}
      </span>
    ) : null;

  const neverChip =
    pulse.status === "NEVER" ? (
      <span className="border-border bg-muted text-muted-foreground inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium">
        {t("never")}
      </span>
    ) : null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-label={t(`status.${pulse.status}` as const)}
            className={cn(
              "inline-block size-2 rounded-full shadow-[0_0_0_2px_var(--background)]",
              PULSE_BG[pulse.status]
            )}
          />
        </TooltipTrigger>
        <TooltipContent>
          <div className="flex flex-col gap-0.5">
            <span className="font-medium">{t(`status.${pulse.status}` as const)}</span>
            {pulse.message ? (
              <span className="text-muted-foreground text-xs">{pulse.message}</span>
            ) : null}
          </div>
        </TooltipContent>
      </Tooltip>
      {ageBadge}
      {failedLink}
      {staleChip}
      {neverChip}
    </div>
  );
}
