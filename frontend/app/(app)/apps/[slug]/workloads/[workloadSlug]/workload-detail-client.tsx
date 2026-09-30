"use client";

import { useWorkloadDetail } from "@/components/screens/apps/workloads/use-workload-detail";
import { WorkloadDetailScreen } from "@/components/screens/apps/workloads/WorkloadDetailScreen";

import { useAppChrome } from "@/lib/app-chrome-context";
import { ManifestCard } from "./manifest-card";
import { ResourceUsageGauges } from "./resource-usage-gauges";
import { ScalingCard } from "./scaling-card";

/**
 * One workload's detail page. The screen owns the markup; the resource,
 * scaling and manifest cards each poll their own query, so they get
 * containers and are handed in as slots.
 */
export function WorkloadDetailClient({
  appSlug,
  workloadSlug,
}: {
  appSlug: string;
  workloadSlug: string;
}) {
  const chrome = useAppChrome();
  const detail = useWorkloadDetail(appSlug, workloadSlug);
  const w = detail.workload;
  return (
    <WorkloadDetailScreen
      {...detail}
      appSlug={appSlug}
      workloadSlug={workloadSlug}
      basePath={chrome.basePath}
      resourceUsage={
        <ResourceUsageGauges appSlug={appSlug} workloadSlug={workloadSlug} environmentName={null} />
      }
      scaling={
        w ? (
          <ScalingCard
            workloadId={w.id}
            appSlug={appSlug}
            workloadSlug={workloadSlug}
            environmentName={null}
            permission={w.viewerCan?.scale}
            version={w.version}
          />
        ) : null
      }
      manifest={
        <ManifestCard appSlug={appSlug} workloadSlug={workloadSlug} environmentName={null} />
      }
    />
  );
}
