"use client";

/**
 * One series of a Home chart panel: its legend (a swatch in the series'
 * colour and what it counts), its figure in mono, and the compact chart
 * under them, full width. Traffic & errors and Runs & spend stack these
 * inside a Panel. Static SVG, so reduced motion changes nothing. Pure.
 */

import { useTranslations } from "next-intl";

import { MiniBar } from "@/components/viz/mini-bar";
import { Sparkline } from "@/components/viz/sparkline";
import { cn } from "@/lib/utils";

export type SeriesTone = "primary" | "secondary" | "danger";

const TONE: Record<SeriesTone, { text: string; swatch: string }> = {
  primary: { text: "text-chart-1", swatch: "bg-chart-1" },
  secondary: { text: "text-chart-2", swatch: "bg-chart-2" },
  danger: { text: "text-danger-fg", swatch: "bg-danger-fg" },
};

export interface ChartSeriesProps {
  /** What the series counts: the legend's words. */
  label: string;
  /** The headline figure, formatted; a dash when unknown. */
  value: string | null;
  /** A few words after the figure: "now", "7 days", "avg". */
  hint?: string;
  tone: SeriesTone;
  /** A line for a rate over time, bars for counts per day. */
  kind: "line" | "bars";
  data: number[];
  className?: string;
}

export function ChartSeries({ label, value, hint, tone, kind, data, className }: ChartSeriesProps) {
  const home = useTranslations("home");
  const t = TONE[tone];
  const aria = `${label}: ${value ?? home("copy.unknownValue")}${hint ? ` ${hint}` : ""}`;
  return (
    <div className={cn("min-w-0", className)}>
      <div className="flex min-w-0 items-baseline gap-2">
        <span className={cn("size-2 shrink-0 rounded-sm", t.swatch)} aria-hidden />
        <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs" title={label}>
          {label}
        </span>
        <span className="shrink-0 font-mono text-sm tabular-nums">{value ?? "–"}</span>
        {hint && <span className="text-muted-foreground shrink-0 text-xs">{hint}</span>}
      </div>
      {kind === "line" ? (
        <Sparkline
          data={data}
          width={240}
          height={40}
          variant="area"
          className={cn("mt-1 h-10 w-full", t.text)}
          ariaLabel={aria}
        />
      ) : (
        <MiniBar
          data={data}
          width={240}
          height={40}
          gap={4}
          className={cn("mt-1 h-10 w-full", t.text)}
          ariaLabel={aria}
        />
      )}
    </div>
  );
}
