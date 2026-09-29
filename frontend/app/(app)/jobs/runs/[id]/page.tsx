import { PreloadQuery } from "@/lib/apollo";
import { GET_SCHEDULED_JOB_RUN } from "@/graphql/lifecycle/lifecycle.queries";

import { JobRunDetailClient } from "./job-run-detail-client";

export const metadata = { title: "Job run · Astrolift" };

/**
 * Scheduled job run detail (#1106) — drill-in target for a /jobs Runs row.
 * Preloads the run itself, the query the page reads first; the 100-run list
 * window is only an instant-paint fallback when the cache already has it.
 */
export default async function JobRunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PreloadQuery query={GET_SCHEDULED_JOB_RUN} variables={{ id }}>
      <JobRunDetailClient id={id} />
    </PreloadQuery>
  );
}
