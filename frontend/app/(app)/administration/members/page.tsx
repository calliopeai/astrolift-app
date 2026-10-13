import { redirect } from "next/navigation";

import { legacyPeopleHref } from "@/components/screens/administration/access/access-nav";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("people");

/** People moved to Admin › Access (access UX design 5); old links keep working. */
export default async function AdministrationMembersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  redirect(legacyPeopleHref(await searchParams));
}
