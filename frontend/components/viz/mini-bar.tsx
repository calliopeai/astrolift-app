import * as React from "react";

import { cn } from "@/lib/utils";

export interface MiniBarProps {
  /** Bar values, left → right. Normalised to the series max. */
  data: number[];
  width?: number;
  height?: number;
  /** Gap between bars in px. Default 2. */
  gap?: number;
  /**
   * Bar fill via `currentColor` — set a token utility on the parent
   * (`text-chart-1`, `text-success-fg`, …).
   */
  className?: string;
  ariaLabel?: string;
}

/**
 * A compact bar series — deploys/day, runs/hour, and similar small counts.
 *
 * Static SVG rects; pairs with `Sparkline` as the bar-shaped micro-viz. Zero
 * and empty series render nothing but hold their box so layout stays stable.
 */
export function MiniBar({
  data,
  width = 80,
  height = 24,
  gap = 2,
  className,
  ariaLabel,
}: MiniBarProps) {
  const n = data.length;
  const max = n ? Math.max(...data) : 0;
  const barW = n > 0 ? Math.max(1, (width - gap * (n - 1)) / n) : width;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("text-chart-1", className)}
      role="img"
      aria-label={ariaLabel ?? "Distribution"}
    >
      {data.map((v, i) => {
        // A zero bar still shows a 1px sliver so gaps read as "empty", not missing.
        const h = max > 0 ? Math.max(1, (v / max) * height) : 1;
        const x = i * (barW + gap);
        return (
          <rect
            key={i}
            x={x}
            y={height - h}
            width={barW}
            height={h}
            rx={Math.min(1.5, barW / 2)}
            fill="currentColor"
          />
        );
      })}
    </svg>
  );
}
