"use client";

/**
 * Panel: the one panel on detail tabs and dashboards (spec 44 §5.2, §6, §7).
 * A quiet bordered surface with a heading row, sitting on the 12-column
 * `PanelGrid`. The frame never collapses, so a failing panel never blanks
 * the page.
 *
 *   <PanelGrid>
 *     <Panel
 *       title="Latest deploy"
 *       icon={<RocketIcon className="size-4" />}
 *       actions={<Button size="sm" variant="outline">Redeploy</Button>}
 *       span={6}
 *       failure={deploy.failed ? { title: "Deploy failed", reason: deploy.reason } : null}
 *       loading={loading && !data}
 *       error={error?.message}
 *       onRetry={refetch}
 *       empty={{ icon: <RocketIcon />, title: "Not deployed yet", actionHref: "/new", actionLabel: "Deploy" }}
 *     >
 *       …body…
 *     </Panel>
 *   </PanelGrid>
 *
 * Body, in this order: the `failure` reason first (§5.2: a failure puts its
 * reason in the first panel, not behind a click), then one of skeleton while
 * the first fetch is in flight, the error with Retry, the EmptyState, or the
 * children. `flush` drops the body padding for a table that fills the panel.
 *
 * Spans stack to the full row below `xl` (quarters pair up from `lg`), so
 * nothing is squeezed at 768px.
 */

import { AlertTriangleIcon, OctagonAlertIcon } from "lucide-react";
import * as React from "react";

import type { EmptyStateSpec } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** Columns of the 12-column grid the panel takes from `xl` up. */
export type PanelSpan = 3 | 4 | 6 | 8 | 9 | 12;

const SPAN: Record<PanelSpan, string> = {
  12: "col-span-12",
  9: "col-span-12 xl:col-span-9",
  8: "col-span-12 xl:col-span-8",
  6: "col-span-12 xl:col-span-6",
  4: "col-span-12 xl:col-span-4",
  3: "col-span-12 lg:col-span-6 xl:col-span-3",
};

/** Why the entity failed, said before anything else in the panel. */
export interface PanelFailure {
  /** Defaults to "Failed". */
  title?: string;
  /** The reason, verbatim from the system: shown in mono, wraps anywhere. */
  reason: React.ReactNode;
  /** A next step: a link to the logs, a Retry. */
  action?: React.ReactNode;
}

export interface PanelProps {
  title: string;
  icon?: React.ReactNode;
  /** One sentence under the title. */
  description?: React.ReactNode;
  /** Right side of the heading row: a status pill, a window selector, a button. */
  actions?: React.ReactNode;
  span?: PanelSpan;
  /** Shown first in the body, above every state. */
  failure?: PanelFailure | null;
  /** First fetch in flight with nothing to show yet. */
  loading?: boolean;
  /** The skeleton to show while loading. Defaults to three rows. */
  skeleton?: React.ReactNode;
  /** The query failed with nothing to show. Takes the body's place. */
  error?: string | { message: string } | null;
  onRetry?: () => void;
  /** Set when the query succeeded with nothing to show; `actionHref` is the create action. */
  empty?: EmptyStateSpec | null;
  /** No body padding: a table or list that runs edge to edge. */
  flush?: boolean;
  className?: string;
  children?: React.ReactNode;
}

export function Panel({
  title,
  icon,
  description,
  actions,
  span = 12,
  failure,
  loading = false,
  skeleton,
  error,
  onRetry,
  empty,
  flush = false,
  className,
  children,
}: PanelProps) {
  const headingId = React.useId();
  const errorMessage = typeof error === "string" ? error : error?.message;
  const state = loading ? "loading" : errorMessage ? "error" : empty ? "empty" : "ready";
  return (
    <section
      aria-labelledby={headingId}
      aria-busy={loading || undefined}
      className={cn(
        "bg-card text-card-foreground flex min-w-0 flex-col rounded-md border",
        SPAN[span],
        className
      )}
    >
      <div className="flex min-w-0 items-start gap-2 border-b px-4 py-3">
        {icon && (
          <span className="text-muted-foreground mt-0.5 shrink-0" aria-hidden>
            {icon}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h2 id={headingId} className="text-sm font-semibold [overflow-wrap:anywhere]">
            {title}
          </h2>
          {description && (
            <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">{description}</p>
          )}
        </div>
        {actions && <div className="flex min-w-0 shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {failure && <FailureReason failure={failure} />}
      <div className={cn("min-w-0 flex-1", !(flush && state === "ready") && "p-4")}>
        {state === "loading" ? (
          (skeleton ?? <SkeletonRows />)
        ) : state === "error" ? (
          <PanelError title={title} message={errorMessage!} onRetry={onRetry} />
        ) : state === "empty" ? (
          <EmptyState {...empty!} />
        ) : (
          children
        )}
      </div>
    </section>
  );
}

/** The grid panels sit on (spec 44 §6): 12 columns, each Panel declares its span. */
export function PanelGrid({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("grid min-w-0 grid-cols-12 gap-4", className)}>{children}</div>;
}

/** Skeleton rows at a list's own row height, for a panel's loading state. */
export function SkeletonRows({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

function FailureReason({ failure }: { failure: PanelFailure }) {
  return (
    <div
      role="alert"
      className="bg-danger/5 border-danger-border flex min-w-0 items-start gap-2 border-b px-4 py-3"
    >
      <OctagonAlertIcon className="text-danger mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-danger-fg text-sm font-medium">{failure.title ?? "Failed"}</p>
        <div className="text-muted-foreground mt-0.5 font-mono text-xs [overflow-wrap:anywhere]">
          {failure.reason}
        </div>
      </div>
      {failure.action && <div className="shrink-0">{failure.action}</div>}
    </div>
  );
}

function PanelError({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 py-6 text-center">
      <AlertTriangleIcon className="text-danger size-5" aria-hidden />
      <div className="min-w-0">
        <p className="font-medium">Could not load {title.toLowerCase()}</p>
        <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
          {message}
        </p>
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
