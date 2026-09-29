import { JobsClient } from "./jobs-client";

export const metadata = { title: "Scheduled jobs · Astrolift" };

/**
 * No `PreloadQuery`: the jobs list walks every cron workload by cursor and
 * reads the recent runs beside it, so the list's skeleton covers the first
 * paint.
 */
export default function JobsPage() {
  return <JobsClient />;
}
