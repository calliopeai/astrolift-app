import * as React from "react";

import { cn } from "@/lib/utils";

export interface SparklineProps {
  /** Series values, oldest → newest. Empty and single-point inputs render a flat baseline. */
  data: number[];
  width?: number;
  height?: number;
  /** `"line"` (default) or `"area"` (line with a soft fill to the baseline). */
  variant?: "line" | "area";
  strokeWidth?: number;
  /**
   * Colour comes from `currentColor` so the parent drives it with a token
   * utility (`text-chart-1`, `text-success-fg`, …). Pass extra classes here.
   */
  className?: string;
  ariaLabel?: string;
}

/**
 * A tiny, axis-less trend line — the leaf viz for stat tiles and table cells.
 *
 * Deliberately hand-rolled SVG rather than a recharts chart: a sparkline is
 * rendered many times per page (one per row / tile), so it must be near-free
 * and static. `chart.tsx` (recharts) remains the tool for full axed charts
 * with tooltips and legends. Token-driven via `currentColor`; no animation,
 * so `prefers-reduced-motion` is a non-issue.
 */
export function Sparkline({
  data,
  width = 80,
  height = 24,
  variant = "line",
  strokeWidth = 1.5,
  className,
  ariaLabel,
}: SparklineProps) {
  // Pad so the stroke never clips at the top/bottom edge.
  const pad = strokeWidth;
  const innerH = Math.max(1, height - pad * 2);

  const max = data.length ? Math.max(...data) : 0;
  const min = data.length ? Math.min(...data) : 0;
  const span = max - min || 1; // avoid /0 when the series is flat
  const stepX = data.length > 1 ? width / (data.length - 1) : width;

  const coords = data.map((v, i) => {
    const x = i * stepX;
    // Invert Y (SVG origin is top-left); a flat series sits on the mid-line.
    const y = span === 0 ? height / 2 : pad + innerH - ((v - min) / span) * innerH;
    return [x, y] as const;
  });

  // A flat baseline keeps layout stable when there's no data yet.
  const line = coords.length
    ? coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")
    : `0,${(height / 2).toFixed(1)} ${width},${(height / 2).toFixed(1)}`;

  const areaPath = coords.length
    ? `M ${coords[0][0].toFixed(1)},${height} ` +
      coords.map(([x, y]) => `L ${x.toFixed(1)},${y.toFixed(1)}`).join(" ") +
      ` L ${coords[coords.length - 1][0].toFixed(1)},${height} Z`
    : "";

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("text-chart-1 overflow-visible", className)}
      role="img"
      aria-label={ariaLabel ?? "Trend"}
      preserveAspectRatio="none"
    >
      {variant === "area" && areaPath && (
        <path d={areaPath} fill="currentColor" fillOpacity={0.12} stroke="none" />
      )}
      <polyline
        points={line}
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
