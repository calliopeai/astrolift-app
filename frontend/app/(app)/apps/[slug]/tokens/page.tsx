import { redirect } from "next/navigation";

import { redirectTarget, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";

/**
 * Deploy tokens are Access › Deploy tokens now (spec 44 §5.2). The old URL
 * keeps resolving, with whatever query it carried.
 */
export default async function AppTokensRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  redirect(redirectTarget(slug, "access", "tokens", await searchParams));
}
