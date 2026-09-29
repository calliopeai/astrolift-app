"use client";

import { JobRunsScreen } from "@/components/screens/jobs/JobRunsScreen";
import { useJobRuns } from "@/components/screens/jobs/use-job-runs";

export function JobRunsClient() {
  return <JobRunsScreen {...useJobRuns()} />;
}
