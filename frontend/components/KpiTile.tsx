import type { LucideIcon } from "lucide-react";

import { StatTile } from "@/components/ui/stat-tile";

/**
 * Dashboard KPI tile (#435). Thin adapter over the shared {@link StatTile}
 * primitive — kept for the dashboard's empty-install affordance: when
 * ``value === 0`` and ``emptyCta`` is set, a subdued caption surfaces below
 * the number to remove the "is this clickable?" ambiguity new operators run
 * into on an empty install (registry → "Register your first app",
 * clusters → "Add a cluster", etc.).
 *
 * New surfaces should reach for `StatTile` directly; it carries the same
 * label + value + icon + trend contract plus a `sparkline` slot.
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

export function KpiTile({ label, icon, value, loading, href, emptyCta, trend }: KpiTileProps) {
  const isEmpty = typeof value === "number" && value === 0;

  return (
    <StatTile
      label={label}
      icon={icon}
      value={value}
      loading={loading}
      href={href}
      trend={trend}
      footer={isEmpty && emptyCta ? emptyCta : undefined}
    />
  );
}
