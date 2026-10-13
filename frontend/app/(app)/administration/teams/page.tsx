import { redirect } from "next/navigation";

import { legacyTeamsHref } from "@/components/screens/administration/access/access-nav";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("teams");

/**
 * Teams moved to Admin › Access (access UX design 5). `?team=<slug>` (the
 * workspace nav tree's link) opens that team's page.
 */
export default async function AdministrationTeamsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  redirect(legacyTeamsHref(await searchParams));
}
