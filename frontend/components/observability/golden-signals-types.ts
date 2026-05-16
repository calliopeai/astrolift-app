/**
 * Shared types for the App > Observability golden-signals cards (#380).
 *
 * The generated schema enum ``GoldenSignalKind`` is the wire shape;
 * this file adds the FE-only pieces (time-range picker options, the
 * shared empty-state copy, and the unit-formatter dispatcher).
 */

export type TimeRangeKey = "15m" | "1h" | "6h" | "24h" | "7d";

export interface TimeRangeOption {
  key: TimeRangeKey;
  label: string;
  seconds: number;
}

export const TIME_RANGE_OPTIONS: TimeRangeOption[] = [
  { key: "15m", label: "15m", seconds: 15 * 60 },
  { key: "1h", label: "1h", seconds: 60 * 60 },
  { key: "6h", label: "6h", seconds: 6 * 60 * 60 },
  { key: "24h", label: "24h", seconds: 24 * 60 * 60 },
  { key: "7d", label: "7d", seconds: 7 * 24 * 60 * 60 },
];

export const DEFAULT_TIME_RANGE: TimeRangeKey = "1h";

/**
 * Doc-link the empty state callout points at when Prometheus isn't
 * reachable (no endpoint configured on the cluster, transport error,
 * or PromQL failure). The platform docs surface the bootstrap-recipe
 * Prometheus install steps; the URL is kept here so the cards share it.
 */
export const PROMETHEUS_DOC_LINK =
  "https://docs.astrolift.io/runtime/observability/prometheus";

export type GoldenSignalUnit = "rps" | "ratio" | "seconds" | "percent";

/**
 * Format a sample value for tooltip + Y-axis display, given the unit
 * hint the backend returns alongside each signal.
 */
export function formatSignalValue(value: number, unit: string): string {
  switch (unit) {
    case "rps":
      return `${value.toFixed(2)} req/s`;
    case "ratio":
      return `${(value * 100).toFixed(2)}%`;
    case "seconds":
      return value < 1
        ? `${(value * 1000).toFixed(0)} ms`
        : `${value.toFixed(2)} s`;
    case "percent":
      return `${value.toFixed(1)}%`;
    default:
      return value.toFixed(3);
  }
}

/**
 * The HTTP-status-code class palette used by the stacked-area chart.
 * Keys match the backend's ``code_class`` enum.
 */
export const STATUS_CLASS_COLORS: Record<string, string> = {
  "2xx": "var(--chart-2)",
  "3xx": "var(--chart-3)",
  "4xx": "var(--chart-4)",
  "5xx": "var(--chart-1)",
  other: "var(--chart-5)",
};

export const STATUS_CLASS_ORDER = ["2xx", "3xx", "4xx", "5xx", "other"] as const;
