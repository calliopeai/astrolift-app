import Link from "next/link";
import type { LucideIcon } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shared KPI tile used on the dashboard overview grid (#435).
 *
 * Renders a labelled metric in a card with an optional icon and an
 * optional drill-down link. When ``value === 0`` and ``emptyCta`` is
 * provided, a subdued caption surfaces below the number to remove the
 * "is this clickable?" ambiguity new operators run into on an empty
 * install — the CTA text is keyed off the underlying resource
 * (registry → "Register your first app", clusters → "Add a cluster",
 * etc.).
 *
 * Loading state is its own branch so the layout doesn't reflow when
 * data arrives. ``trend`` is rendered beneath the number for tiles
 * that carry a delta indicator (e.g. Cost MTD).
 */
export interface KpiTileProps {
  label: string;
  icon: LucideIcon;
  /**
   * The headline value. Numbers render with tabular numerals; strings
   * (formatted currency, percentages) render as-is. ``null`` /
   * ``undefined`` collapse to ``—``.
   */
  value: number | string | null | undefined;
  loading?: boolean;
  href?: string;
  /**
   * Caption surfaced below the number when ``value === 0``. The
   * dashboard wires per-tile copy via i18n.
   */
  emptyCta?: string;
  /**
   * Trend / delta indicator rendered under the value. The dashboard
   * uses this for the Cost MTD tile (% up/down vs previous month).
   */
  trend?: React.ReactNode;
}

export function KpiTile({
  label,
  icon: Icon,
  value,
  loading,
  href,
  emptyCta,
  trend,
}: KpiTileProps) {
  const isEmpty = typeof value === "number" && value === 0;
  const displayValue =
    value === null || value === undefined ? "—" : typeof value === "number" ? value : value;

  const card = (
    <Card className="hover:bg-accent/40 transition-colors">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
        <div className="bg-primary/10 text-primary rounded-md p-1.5">
          <Icon className="h-4 w-4" />
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-16" />
        ) : (
          <p className="text-2xl font-bold tabular-nums">{displayValue}</p>
        )}
        {trend && !loading && <div className="mt-1">{trend}</div>}
        {isEmpty && emptyCta && !loading && (
          <p className="text-muted-foreground mt-2 text-xs">{emptyCta}</p>
        )}
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
