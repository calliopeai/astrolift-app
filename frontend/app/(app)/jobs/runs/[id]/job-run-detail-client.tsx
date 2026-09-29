"use client";

import { JobRunDetail } from "@/components/screens/jobs/JobRunDetail";
import { useJobRunDetail } from "@/components/screens/jobs/use-job-run-detail";

/** Scheduled job run detail (#1106, #1118). */
export function JobRunDetailClient({ id }: { id: string }) {
  return <JobRunDetail id={id} {...useJobRunDetail(id)} />;
}
