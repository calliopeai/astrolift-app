"use client";

import { ClockIcon, RefreshCwIcon, ShieldCheckIcon } from "lucide-react";
import Link from "next/link";
import { useFormatter, useNow, useTranslations } from "next-intl";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { PendingHumanGate } from "@/graphql/workflows/tiered.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { usePendingGates } from "./use-pending-gates";

export type PendingGatesScreenProps = ReturnType<typeof usePendingGates>;

/** Deep link into the run's observe page, where #2068's GateReview decides it. */
function reviewHref(gate: PendingHumanGate): string {
  return `/workflows/${encodeURIComponent(gate.definitionSlug)}/observe?run=${encodeURIComponent(gate.runGuid)}`;
}

/** Org-wide pending human gates (#1820); each row links to its run's observe page. */
export function PendingGatesScreen({ gates, loading, error, onRefresh }: PendingGatesScreenProps) {
  const fmt = useFormatters();
  const t = useTranslations("approvals.pendingGates");
  const now = useNow();

  if (loading && gates.length === 0) {
    return (
      <PageShell title={t("title")} description={t("loadingDescription")}>
        <Skeleton className="h-24 w-full" />
      </PageShell>
    );
  }

  if (error) {
    return (
      <PageShell title={t("title")} description={t("loadingDescription")}>
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
          {error}
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      actions={
        <Button variant="outline" size="icon" onClick={onRefresh} aria-label={t("refresh")}>
          <RefreshCwIcon className="size-4" />
        </Button>
      }
    >
      {gates.length === 0 ? (
        <EmptyState
          title={t("emptyTitle")}
          description={t("emptyDescription")}
          icon={<ShieldCheckIcon className="size-6" aria-hidden />}
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {gates.map((gate) => (
            <li key={gate.executionId + gate.runGuid}>
              <GateRow gate={gate} formatRelative={(date) => fmt.formatRelativeTime(date, now)} />
            </li>
          ))}
        </ul>
      )}
    </PageShell>
  );
}

function GateRow({
  gate,
  formatRelative,
}: {
  gate: PendingHumanGate;
  formatRelative: (d: Date | string, now?: Date) => string;
}) {
  const t = useTranslations("approvals.pendingGates");
  const fmt = useFormatter();
  return (
    <div className="bg-background flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <ShieldCheckIcon className="text-muted-foreground size-4 shrink-0" aria-hidden />
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={reviewHref(gate)} className="font-medium hover:underline">
              {gate.definitionName || gate.definitionSlug}
            </Link>
            {gate.stageRole && (
              <Badge variant="outline" className="text-2xs">
                {gate.stageRole}
              </Badge>
            )}
          </div>
          <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            {gate.startedAt && (
              <span className="inline-flex items-center gap-1">
                <ClockIcon className="size-3" aria-hidden />
                {t("waitingSince", { time: formatRelative(gate.startedAt) })}
              </span>
            )}
            {gate.stageApprovers.length > 0 && (
              <span>
                {t("approvers", { names: fmt.list(gate.stageApprovers, { type: "conjunction" }) })}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button asChild size="sm" variant="outline" className="min-h-11 w-full sm:w-auto">
          <Link href={reviewHref(gate)}>{t("review")}</Link>
        </Button>
      </div>
    </div>
  );
}
