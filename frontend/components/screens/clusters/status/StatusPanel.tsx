"use client";

import { AlertTriangleIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * A panel on the cluster tabs' 12-column grid (spec 44 §5.2, §6): a quiet
 * bordered surface with a heading row, and the shared states inside it, in
 * this order: skeleton while the first fetch is in flight, the error with a
 * retry, the EmptyState, then the body. The frame never collapses, so a
 * failing card never blanks the page.
 *
 * Local to the cluster tabs until a shared `Panel` primitive lands (§7).
 */

export interface StatusPanelEmpty {
  icon: React.ReactNode;
  title: string;
  description?: string;
}

export interface StatusPanelProps {
  icon: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  /** Right side of the heading row: a status pill, a window selector. */
  action?: React.ReactNode;
  /** Grid placement: the full row, or half of it from `xl` up. */
  span?: "full" | "half";
  /** First fetch in flight with nothing to show yet. */
  loading?: boolean;
  /** The skeleton to show while loading. Defaults to three rows. */
  skeleton?: React.ReactNode;
  /** The query failed with nothing to show. Takes the body's place. */
  error?: string | null;
  onRetry?: () => void;
  /** Set when the query succeeded with nothing to show. */
  empty?: StatusPanelEmpty | null;
  children?: React.ReactNode;
}

const SPAN = {
  full: "col-span-12",
  half: "col-span-12 xl:col-span-6",
} as const;

export function StatusPanel({
  icon,
  title,
  description,
  action,
  span = "full",
  loading = false,
  skeleton,
  error,
  onRetry,
  empty,
  children,
}: StatusPanelProps) {
  const headingId = React.useId();
  return (
    <section
      aria-labelledby={headingId}
      aria-busy={loading || undefined}
      className={cn(
        "bg-card text-card-foreground flex min-w-0 flex-col rounded-md border",
        SPAN[span]
      )}
    >
      <div className="flex min-w-0 items-start gap-2 border-b px-4 py-3">
        <span className="text-muted-foreground mt-0.5 shrink-0" aria-hidden>
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h2 id={headingId} className="text-sm font-semibold [overflow-wrap:anywhere]">
            {title}
          </h2>
          {description && (
            <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">{description}</p>
          )}
        </div>
        {action && <div className="min-w-0 shrink-0">{action}</div>}
      </div>
      <div className="min-w-0 flex-1 p-4">
        {loading ? (
          (skeleton ?? <SkeletonRows />)
        ) : error ? (
          <PanelError title={title} message={error} onRetry={onRetry} />
        ) : empty ? (
          <EmptyState icon={empty.icon} title={empty.title} description={empty.description} />
        ) : (
          children
        )}
      </div>
    </section>
  );
}

/** The grid the panels sit on. */
export function StatusPanelGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid min-w-0 grid-cols-12 gap-4">{children}</div>;
}

export function SkeletonRows({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
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
