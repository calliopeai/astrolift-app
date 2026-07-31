import { JobsClient } from "./jobs-client";

export const metadata = { title: "Jobs · Astrolift" };

/**
 * No `PreloadQuery`: every table on this page walks a cursor whose first
 * request carries `limit`, a search term and (for two tabs) a filter, so a
 * variable-less preload of the deprecated flat list was a guaranteed cache
 * miss — an SSR round trip *and* the client fetch. DataTable's skeleton
 * covers the first paint.
 */
export default function JobsPage() {
  return <JobsClient />;
}
