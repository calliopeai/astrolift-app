"use client";

import { ScalingCardView } from "@/components/screens/apps/workloads/ScalingCard";
import { useWorkloadScaling } from "@/components/screens/apps/workloads/use-workload-scaling";

/** The scaling card, wired to its query and the scale mutation. The view lives in components/screens/apps/workloads/ScalingCard. */
export function ScalingCard(props: {
  workloadId: string;
  appSlug: string;
  workloadSlug: string;
  environmentName: string | null;
  canDeploy: boolean;
}) {
  return <ScalingCardView {...useWorkloadScaling(props)} />;
}
