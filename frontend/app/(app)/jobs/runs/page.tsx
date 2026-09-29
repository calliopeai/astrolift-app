import { JobRunsClient } from "./job-runs-client";

export const metadata = { title: "Job runs · Astrolift" };

/** Every job run, or one job's (`?app=&workload=`), cursor paged. */
export default function JobRunsPage() {
  return <JobRunsClient />;
}
