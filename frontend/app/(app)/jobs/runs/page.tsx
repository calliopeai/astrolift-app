import { routeTitle } from "@/lib/i18n/route-title";

import { JobRunsClient } from "./job-runs-client";

export const generateMetadata = routeTitle("jobRuns");

/** Every job run, or one job's (`?app=&workload=`), cursor paged. */
export default function JobRunsPage() {
  return <JobRunsClient />;
}
