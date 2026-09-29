import { redirect } from "next/navigation";

import { redirectTarget, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";

/**
 * Previews are a view of Deployments now (`?view=previews`) (spec 44 §5.2).
 * The old URL keeps resolving, with whatever query it carried.
 */
export default async function AppPreviewsRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  redirect(redirectTarget(slug, "deployments", "previews", await searchParams));
}
