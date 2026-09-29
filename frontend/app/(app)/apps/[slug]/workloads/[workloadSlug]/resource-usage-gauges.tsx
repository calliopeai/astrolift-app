"use client";

import { ResourceUsageGaugesView } from "@/components/screens/apps/workloads/ResourceUsageGauges";
import { useWorkloadResourceUsage } from "@/components/screens/apps/workloads/use-workload-resource-usage";

/** Live resource usage gauges, wired to their query. The view lives in components/screens/apps/workloads/ResourceUsageGauges. */
export function ResourceUsageGauges({
  appSlug,
  workloadSlug,
  environmentName,
}: {
  appSlug: string;
  workloadSlug: string;
  environmentName: string | null;
}) {
  return (
    <ResourceUsageGaugesView
      {...useWorkloadResourceUsage(appSlug, workloadSlug, environmentName)}
    />
  );
}
