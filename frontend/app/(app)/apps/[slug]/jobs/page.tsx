import { JobsClient } from "@/app/(app)/jobs/jobs-client";

import { AppTabs } from "../components/app-tabs";

export const metadata = { title: "Jobs · App · Astrolift" };

/**
 * No `PreloadQuery`: the tables here walk cursors whose variables the
 * controller owns (limit, search, per-tab filter), so a preload of the
 * deprecated flat list fed nothing. See `app/(app)/jobs/page.tsx`.
 */
export default async function AppJobsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <JobsClient appSlug={slug} tabs={<AppTabs slug={slug} active="deployments" />} />;
}
