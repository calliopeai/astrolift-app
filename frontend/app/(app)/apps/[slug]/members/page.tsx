import { redirect } from "next/navigation";

import { redirectTarget, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";

/**
 * Members are Access › Members now (spec 44 §5.2). The old URL keeps
 * resolving, with whatever query it carried.
 */
export default async function AppMembersRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  redirect(redirectTarget(slug, "access", "members", await searchParams));
}
