import { redirect } from "next/navigation";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";
import { parsePrincipalParam } from "@/components/screens/administration/access/principal-tabs";
import { routeTitle } from "@/lib/i18n/route-title";

import { GroupMembersClient } from "../principal-clients";

export const generateMetadata = routeTitle("members", "people");

/** An IdP group's Members tab. A person has Teams instead. */
export default async function GroupMembersPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const who = parsePrincipalParam(id);
  if (who.kind === "user") redirect(`${PEOPLE_HREF}/${id}/teams`);
  return <GroupMembersClient externalId={who.externalId} />;
}
