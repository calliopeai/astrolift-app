import { routeTitle } from "@/lib/i18n/route-title";

import { JobsClient } from "./jobs-client";

export const generateMetadata = routeTitle("scheduledJobs");

/**
 * No `PreloadQuery`: the jobs page query lives in its client hook
 * (use-jobs.ts), so the list's skeleton covers the first paint.
 */
export default function JobsPage() {
  return <JobsClient />;
}
