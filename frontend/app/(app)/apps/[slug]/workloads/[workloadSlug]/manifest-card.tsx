"use client";

import { ManifestCardView } from "@/components/screens/apps/workloads/ManifestCard";
import { useWorkloadManifest } from "@/components/screens/apps/workloads/use-workload-manifest";

/** The rendered-manifest card, wired to its query. The view lives in components/screens/apps/workloads/ManifestCard. */
export function ManifestCard({
  appSlug,
  workloadSlug,
  environmentName,
}: {
  appSlug: string;
  workloadSlug: string;
  environmentName: string | null;
}) {
  return <ManifestCardView {...useWorkloadManifest(appSlug, workloadSlug, environmentName)} />;
}
