"use client";

import { MetricsScreen } from "@/components/screens/metrics/MetricsScreen";
import { useMetrics } from "@/components/screens/metrics/use-metrics";

export function MetricsClient() {
  return (
    <MetricsScreen {...useMetrics()} temporalUiUrl={process.env.NEXT_PUBLIC_TEMPORAL_UI_URL} />
  );
}
