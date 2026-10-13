import { redirect } from "next/navigation";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";
import { parsePrincipalParam } from "@/components/screens/administration/access/principal-tabs";
import { routeTitle } from "@/lib/i18n/route-title";

import { PersonActivityClient } from "../principal-clients";

export const generateMetadata = routeTitle("activity", "people");

/** A person's Activity tab. A group has no activity of its own. */
export default async function PersonActivityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const who = parsePrincipalParam(id);
  if (who.kind === "group") redirect(`${PEOPLE_HREF}/${id}`);
  return <PersonActivityClient memberId={who.memberId} />;
}
