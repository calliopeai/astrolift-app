import { redirect } from "next/navigation";

import { redirectTarget, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";

/**
 * Topology is a panel on Overview now (spec 44 §5.2). The old URL keeps
 * resolving, with whatever query it carried.
 */
export default async function AppTopologyRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  redirect(redirectTarget(slug, "overview", null, await searchParams));
}
