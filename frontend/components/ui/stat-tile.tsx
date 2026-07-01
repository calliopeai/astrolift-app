import Link from "next/link";
import type { LucideIcon } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export interface StatTileProps {
  /** Metric label (e.g. "Running apps"). */
  label: React.ReactNode;
  /**
   * Headline value. Numbers render with tabular numerals; strings
   * (formatted currency, percentages) render as-is. `null` / `undefined`
   * collapse to `—`.
   */
  value: number | string | null | undefined;
  /** Optional leading icon, shown in a tinted chip. */
  icon?: LucideIcon;
  /**
   * Optional inline visual rendered beside the value — the wire-in point
   * for the `Sparkline` primitive (#B1). Keep it compact; the value stays
   * the focal point.
   */
  sparkline?: React.ReactNode;
  /** Delta / trend indicator rendered under the value. */
  trend?: React.ReactNode;
  /** Subdued caption under the value (e.g. an empty-state CTA). */
  footer?: React.ReactNode;
  loading?: boolean;
  /** When set, the whole tile becomes a drill-down link. */
  href?: string;
  className?: string;
}

/**
 * A single metric in a bounded `Card` — the canonical dense stat surface.
 *
 * Promoted from the dashboard's `KpiTile` into a shared primitive so the
 * whole platform has one stat tile (label + value + optional icon / trend /
 * sparkline). Sits at the leaf of the container rule
 * (`Section > StatTile`); never wrap a `Section` around it. For a KPI grid,
 * map an array of these inside a single `Section`.
 *
 * @example
 * <Section title="Fleet">
 *   <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
 *     <StatTile label="Running apps" value={12} icon={Rocket} href="/apps" />
 *     <StatTile label="Cost MTD" value="$1,204" trend={<TrendStat …/>} />
 *   </div>
 * </Section>
 */
export function StatTile({
  label,
  value,
  icon: Icon,
  sparkline,
  trend,
  footer,
  loading,
  href,
  className,
}: StatTileProps) {
  const displayValue = value === null || value === undefined ? "—" : value;

  const card = (
    <Card className={cn("hover:bg-accent/40 transition-colors", className)}>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
        {Icon && (
          <div className="bg-primary/10 text-primary rounded-md p-1.5">
            <Icon className="h-4 w-4" />
          </div>
        )}
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-16" />
        ) : (
          <div className="flex items-end justify-between gap-3">
            <p className="text-2xl font-bold tabular-nums">{displayValue}</p>
            {sparkline && <div className="min-w-0 shrink">{sparkline}</div>}
          </div>
        )}
        {trend && !loading && <div className="mt-1">{trend}</div>}
        {footer && !loading && <p className="text-muted-foreground mt-2 text-xs">{footer}</p>}
      </CardContent>
    </Card>
  );

  if (href) {
    return (
      <Link href={href} className="contents">
        {card}
      </Link>
    );
  }
  return card;
}
