import { PreloadQuery } from "@/lib/apollo";
import { LIST_SCHEDULED_JOB_RUNS } from "@/graphql/lifecycle/lifecycle.queries";

import { JobRunDetailClient } from "./job-run-detail-client";

export const metadata = { title: "Job run · Astrolift" };

/**
 * Scheduled job run detail (#1106) — drill-in target for a /jobs Runs row.
 * Reuses the global LIST_SCHEDULED_JOB_RUNS window (no singular query exists).
 */
export default async function JobRunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_SCHEDULED_JOB_RUNS} variables={{ limit: 100 }}>
      <JobRunDetailClient id={id} />
    </PreloadQuery>
  );
}
