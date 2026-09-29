import { redirect } from "next/navigation";

import { redirectTarget, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";

/**
 * Scheduled jobs are the cronjob kind on Workloads now (`?kind=cronjob`)
 * (spec 44 §5.2). The old URL keeps resolving, with whatever query it
 * carried.
 */
export default async function AppJobsRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  redirect(redirectTarget(slug, "workloads", "jobs", await searchParams));
}
