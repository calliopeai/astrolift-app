import { redirect } from "next/navigation";

import { redirectTarget, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";

/**
 * The old Console tab's logs half is what deep links point at (PodExpander,
 * bookmarks, incident threads), so it lands on Logs & metrics › Logs (spec
 * 44 §5.2). The old URL keeps resolving, with whatever query it carried.
 */
export default async function AppConsoleRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  redirect(redirectTarget(slug, "logs", "logs", await searchParams));
}
