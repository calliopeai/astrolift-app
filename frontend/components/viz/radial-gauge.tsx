import * as React from "react";

import { cn } from "@/lib/utils";

export interface RadialGaugeProps {
  /** Ratio in `[0, 1]`. Values outside are clamped. */
  value: number;
  size?: number;
  thickness?: number;
  /**
   * Centre label. Defaults to the value as a rounded percentage; pass `null`
   * to hide it or a node to override (e.g. "3/4").
   */
  label?: React.ReactNode | null;
  /**
   * Progress-arc colour. Defaults to `currentColor` (set a token on the
   * parent). For status thresholds pass one of the `*-fg` token classes.
   */
  className?: string;
  ariaLabel?: string;
}

/**
 * A ring gauge for ratios — success rate, quota utilisation, rollout progress.
 *
 * Static SVG (two stacked circles via `stroke-dasharray`); no animation, so
 * `prefers-reduced-motion` needs no special-casing. Track uses the muted
 * token; the progress arc inherits `currentColor`.
 */
export function RadialGauge({
  value,
  size = 64,
  thickness = 6,
  label,
  className,
  ariaLabel,
}: RadialGaugeProps) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.round(clamped * 100);

  const centre = label === null ? null : (label ?? `${pct}%`);

  return (
    <div
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={ariaLabel ?? `${pct}%`}
        // Start the arc at 12 o'clock and sweep clockwise.
        className="-rotate-90"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={thickness}
          className="text-muted stroke-current"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - clamped)}
        />
      </svg>
      {centre !== null && (
        <span className="absolute text-xs font-semibold tabular-nums">{centre}</span>
      )}
    </div>
  );
}
