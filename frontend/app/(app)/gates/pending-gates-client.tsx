"use client";

import { ClockIcon, RefreshCwIcon, ShieldCheckIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePendingHumanGates } from "@/graphql/workflows/tiered.hooks";
import type { PendingHumanGate } from "@/graphql/workflows/tiered.types";
import { useFormatters } from "@/lib/i18n/formatters";

// Freshness matches the approvals queue (#420): frequent enough that a
// newly-opened gate shows up without a manual refresh, not so tight it
// hammers the server for a list that changes on human timescales.
const POLL_MS = 30_000;

/** Deep link into the run's observe page, where #2068's GateReview decides it. */
function reviewHref(gate: PendingHumanGate): string {
  return `/workflows/${encodeURIComponent(gate.definitionSlug)}/observe?run=${encodeURIComponent(gate.runGuid)}`;
}

export function PendingGatesClient() {
  const fmt = useFormatters();
  const { gates, loading, error, refetch } = usePendingHumanGates({ pollInterval: POLL_MS });

  if (loading && gates.length === 0) {
    return (
      <PageShell
        title="Pending Gates"
        description="Human gates across the organization waiting on a decision."
      >
        <Skeleton className="h-24 w-full" />
      </PageShell>
    );
  }

  if (error) {
    return (
      <PageShell
        title="Pending Gates"
        description="Human gates across the organization waiting on a decision."
      >
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
          {error.message}
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell
      title="Pending Gates"
      description="Every open human_gate stage across the organization's workflow runs that you may decide, newest first."
      actions={
        <Button
          variant="outline"
          size="icon"
          onClick={() => refetch()}
          aria-label="Refresh pending gates"
        >
          <RefreshCwIcon className="size-4" />
        </Button>
      }
    >
      {gates.length === 0 ? (
        <EmptyState
          title="No pending gates"
          description="Nothing is waiting on your review right now."
          icon={<ShieldCheckIcon className="size-6" aria-hidden />}
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {gates.map((gate) => (
            <li key={gate.executionId + gate.runGuid}>
              <GateRow gate={gate} formatRelative={fmt.formatRelativeTime} />
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
                Waiting since {formatRelative(gate.startedAt)}
              </span>
            )}
            {gate.stageApprovers.length > 0 && (
              <span>Approvers: {gate.stageApprovers.join(", ")}</span>
            )}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button asChild size="sm" variant="outline" className="min-h-11 w-full sm:w-auto">
          <Link href={reviewHref(gate)}>Review</Link>
        </Button>
      </div>
    </div>
  );
}
