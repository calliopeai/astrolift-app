import { ArrowDownIcon, ArrowRightIcon, ArrowUpIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

import { Sparkline } from "./sparkline";

export interface TrendStatProps {
  /** Formatted headline value (currency, count, %…). */
  value: React.ReactNode;
  /** Signed delta; its sign drives the arrow and colour. */
  delta?: number | null;
  /** Trailing caption beside the delta, e.g. "vs prev month". */
  deltaLabel?: React.ReactNode;
  /**
   * Flip the colour semantics: by default a rising value is "good"
   * (success/green) and a falling one "bad" (danger/red). For metrics where
   * up is bad — cost, error rate — set `invert` so the palette matches intent.
   */
  invert?: boolean;
  /** Optional inline sparkline series drawn beside the value. */
  data?: number[];
  /** Format the delta magnitude. Default: `12.3%`. */
  formatDelta?: (n: number) => string;
  className?: string;
}

const defaultFormatDelta = (n: number) => `${Math.abs(n).toFixed(1)}%`;

/**
 * Value + signed delta (+ optional sparkline) — the canonical KPI trend row.
 *
 * Generalises the dashboard's bespoke cost-delta indicator into one primitive
 * that composes with `StatTile` (via its `trend` / `sparkline` slots) and
 * stands alone. Colour is driven by the #A1 status tokens so it reads
 * correctly in light and dark.
 */
export function TrendStat({
  value,
  delta,
  deltaLabel,
  invert = false,
  data,
  formatDelta = defaultFormatDelta,
  className,
}: TrendStatProps) {
  const hasDelta = delta !== null && delta !== undefined;
  const rising = hasDelta && delta! > 0;
  const falling = hasDelta && delta! < 0;

  const Arrow = rising ? ArrowUpIcon : falling ? ArrowDownIcon : ArrowRightIcon;
  // "good" = rising unless inverted. Flat → muted.
  const good = rising !== invert; // XOR: rising && !invert, or falling && invert
  const tone = !hasDelta || (!rising && !falling)
    ? "text-muted-foreground"
    : good
      ? "text-success-fg"
      : "text-danger-fg";

  return (
    <div className={cn("flex items-end justify-between gap-3", className)}>
      <div className="min-w-0">
        <div className="text-2xl font-bold tabular-nums">{value}</div>
        {hasDelta && (
          <div className={cn("mt-0.5 inline-flex items-center gap-1 text-xs", tone)}>
            <Arrow className="size-3" />
            <span className="font-mono">{formatDelta(delta!)}</span>
            {deltaLabel && <span className="text-muted-foreground">{deltaLabel}</span>}
          </div>
        )}
      </div>
      {data && data.length > 0 && (
        <Sparkline data={data} variant="area" className={cn("shrink-0", tone)} ariaLabel="Trend" />
      )}
    </div>
  );
}
