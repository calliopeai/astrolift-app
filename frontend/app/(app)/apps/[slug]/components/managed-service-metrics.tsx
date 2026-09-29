"use client";

import { ManagedServiceMetricsPanel } from "@/components/observability/ManagedServiceMetricsPanel";
import { useManagedServiceMetrics } from "@/components/observability/use-managed-service-metrics";

/** One managed service's metrics panel, wired to its query. */
export function ManagedServiceMetrics({ managedServiceId }: { managedServiceId: string }) {
  return <ManagedServiceMetricsPanel {...useManagedServiceMetrics(managedServiceId)} />;
}
