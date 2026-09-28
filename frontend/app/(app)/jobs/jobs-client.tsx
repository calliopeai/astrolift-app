"use client";

import { JobsScreen } from "@/components/screens/jobs/JobsScreen";
import { useJobs } from "@/components/screens/jobs/use-jobs";

/**
 * Scheduled jobs, fleet-wide at /jobs and embedded on an app's Workloads
 * tab (`?kind=cronjob`) when `appSlug` is set.
 */
export function JobsClient({ appSlug }: { appSlug?: string } = {}) {
  return <JobsScreen {...useJobs(appSlug)} />;
}
