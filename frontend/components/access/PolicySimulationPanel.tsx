"use client";

import { AlertTriangleIcon, FlaskConicalIcon } from "lucide-react";
import type * as React from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftPolicySimulation,
  AstroliftPolicySimulationDecision,
  AstroliftPolicySimulationHolder,
} from "@/graphql/__generated__/schema";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

import { PrincipalChip } from "./PrincipalChip";

type Holder = Pick<
  AstroliftPolicySimulationHolder,
  "outcome" | "denied" | "unknown" | "sourceScopeLabel" | "detail"
> & {
  user: { id: string; username: string; email: string };
  memberId?: string | null;
};

type Decision = Pick<
  AstroliftPolicySimulationDecision,
  "id" | "occurredAt" | "action" | "actorDisplay" | "outcome" | "detail"
>;

/** What `astroliftPolicySimulation` answers, as far as the panel reads it. */
export type PolicySimulation = Pick<
  AstroliftPolicySimulation,
  | "ok"
  | "errors"
  | "actions"
  | "holdersCount"
  | "holdersDeniedCount"
  | "holdersUnknownCount"
  | "auditRecorded"
  | "windowDays"
  | "decisionsEvaluated"
  | "decisionsDeniedCount"
  | "decisionsUnknownCount"
  | "notes"
> & { holders: Holder[]; decisions: Decision[] };

export interface PolicySimulationPanelProps {
  simulation: PolicySimulation | null;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  /** Where a holder's page is, to see the rest of their access. */
  personHref?: (memberId: string) => string;
  className?: string;
}

const OUTCOME_LABEL: Record<string, string> = {
  DENIED: "denied",
  UNKNOWN: "denied, cannot tell",
  NOT_DENIED: "not denied",
};

/**
 * A draft policy run before it is saved (design 3.6): who holds what it
 * covers today and would be denied, who it cannot decide for (a condition
 * the check cannot answer denies, so those are denied too), and which of the
 * org's recorded decisions in the window would have gone the other way.
 * The server's own evaluator answers it (`astroliftPolicySimulation`), so
 * the review says what saving would do. Pure.
 */
export function PolicySimulationPanel({
  simulation,
  loading = false,
  error,
  onRetry,
  personHref,
  className,
}: PolicySimulationPanelProps) {
  const fmt = useFormatters();
  const frame = cn("flex min-w-0 flex-col gap-3", className);

  if (loading) {
    return (
      <section aria-label="Simulation" aria-busy className={frame}>
        <Heading />
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-20" />
      </section>
    );
  }
  if (error || (simulation && !simulation.ok)) {
    const message = error ?? simulation?.errors.join(" ") ?? "The simulation failed.";
    return (
      <section aria-label="Simulation" className={frame}>
        <Heading />
        <div
          role="alert"
          className="border-warning-border bg-warning-bg text-warning-fg flex min-w-0 flex-wrap items-center gap-2 rounded-md border p-3 text-sm"
        >
          <AlertTriangleIcon className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
            Could not simulate: {message}
          </span>
          {onRetry && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              Retry
            </Button>
          )}
        </div>
      </section>
    );
  }
  if (!simulation) return null;

  const s = simulation;
  const affected = s.holders.filter((h) => h.outcome !== "NOT_DENIED");
  const flipped = s.decisions;

  return (
    <section aria-label="Simulation" className={frame}>
      <Heading />
      <p className="text-sm [overflow-wrap:anywhere]" data-testid="simulation-summary">
        {holderSentence(s)}
      </p>
      {s.actions.length > 0 && (
        <p className="text-muted-foreground text-2xs min-w-0 font-mono [overflow-wrap:anywhere]">
          Covers {s.actions.join(", ")}
        </p>
      )}

      {affected.length > 0 && (
        <List title="Holders it would deny">
          {affected.map((h) => (
            <li key={h.user.id} className="flex min-w-0 flex-col gap-1 px-3 py-2">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <PrincipalChip
                  principal={{
                    kind: "user",
                    id: h.user.id,
                    name: h.user.username,
                    detail: h.user.email || undefined,
                    href: h.memberId && personHref ? personHref(h.memberId) : undefined,
                  }}
                />
                <span
                  className={cn(
                    "text-2xs font-mono",
                    h.outcome === "DENIED" ? "text-danger-fg" : "text-warning-fg"
                  )}
                >
                  {OUTCOME_LABEL[h.outcome] ?? h.outcome}
                </span>
                {h.sourceScopeLabel && (
                  <span className="text-muted-foreground text-2xs min-w-0 truncate">
                    at {h.sourceScopeLabel}
                  </span>
                )}
              </div>
              <span className="text-muted-foreground text-2xs min-w-0 font-mono [overflow-wrap:anywhere]">
                {[...h.denied, ...h.unknown].join(", ")}
                {h.detail ? ` · ${h.detail}` : ""}
              </span>
            </li>
          ))}
        </List>
      )}

      <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
        {s.auditRecorded
          ? `Over the last ${s.windowDays} days, ${s.decisionsEvaluated} recorded ${
              s.decisionsEvaluated === 1 ? "decision" : "decisions"
            } would have been checked: ${s.decisionsDeniedCount} denied, ${s.decisionsUnknownCount} denied for want of an attribute.`
          : `This organization has no recorded decisions in the last ${s.windowDays} days, so only today's holders are simulated.`}
      </p>
      {flipped.length > 0 && (
        <List title="Recorded decisions it would have denied">
          {flipped.map((d) => (
            <li
              key={d.id}
              className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
            >
              <time dateTime={d.occurredAt} className="text-muted-foreground font-mono text-xs">
                {fmt.formatDateTime(d.occurredAt)}
              </time>
              <span className="min-w-0 truncate font-mono text-xs" title={d.action}>
                {d.action}
              </span>
              <span className="min-w-0 truncate text-sm" title={d.actorDisplay}>
                {d.actorDisplay}
              </span>
              <span
                className={cn(
                  "text-2xs font-mono",
                  d.outcome === "DENIED" ? "text-danger-fg" : "text-warning-fg"
                )}
              >
                {OUTCOME_LABEL[d.outcome] ?? d.outcome}
              </span>
            </li>
          ))}
        </List>
      )}

      {s.notes.map((n) => (
        <p key={n} className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
          {n}
        </p>
      ))}
    </section>
  );
}

/** "3 of 41 holders would be denied; 1 more depend on something a check cannot always answer…" */
export function holderSentence(s: PolicySimulation): string {
  const n = s.holdersCount;
  if (n === 0) return "Nobody holds what this policy covers today.";
  const lead = `${s.holdersDeniedCount} of ${n} ${n === 1 ? "holder" : "holders"} would be denied`;
  const unknown = s.holdersUnknownCount
    ? `; ${s.holdersUnknownCount} more depend on something a check cannot always answer, and are denied when it cannot`
    : "";
  return `${lead}${unknown}.`;
}

function Heading() {
  return (
    <h3 className="flex items-center gap-2 text-sm font-medium">
      <FlaskConicalIcon aria-hidden className="text-muted-foreground size-4" />
      Simulation
    </h3>
  );
}

function List({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <h4 className="text-muted-foreground text-xs font-medium uppercase">{title}</h4>
      <ul className="max-h-56 min-w-0 divide-y overflow-auto rounded-md border">{children}</ul>
    </div>
  );
}
