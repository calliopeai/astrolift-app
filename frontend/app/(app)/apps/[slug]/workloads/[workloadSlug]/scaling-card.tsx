"use client";

import type { AstroliftActionPermission } from "@/graphql/__generated__/schema";
import { ScalingCardView } from "@/components/screens/apps/workloads/ScalingCard";
import { useWorkloadScaling } from "@/components/screens/apps/workloads/use-workload-scaling";

/** The scaling card, wired to its query and the scale mutation. The view lives in components/screens/apps/workloads/ScalingCard. */
export function ScalingCard(props: {
  workloadId: string;
  appSlug: string;
  workloadSlug: string;
  environmentName: string | null;
  permission: AstroliftActionPermission | undefined;
  version: number | undefined;
}) {
  return <ScalingCardView {...useWorkloadScaling(props)} />;
}
