import { JobsClient } from "./jobs-client";

export const metadata = { title: "Scheduled jobs · Astrolift" };

/**
 * No `PreloadQuery`: the jobs page query lives in its client hook
 * (use-jobs.ts), so the list's skeleton covers the first paint.
 */
export default function JobsPage() {
  return <JobsClient />;
}
