import { redirect } from "next/navigation";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";
import { parsePrincipalParam } from "@/components/screens/administration/access/principal-tabs";

import { GroupMembersClient } from "../principal-clients";

export const metadata = { title: "Members · People · Astrolift" };

/** An IdP group's Members tab. A person has Teams instead. */
export default async function GroupMembersPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const who = parsePrincipalParam(id);
  if (who.kind === "user") redirect(`${PEOPLE_HREF}/${id}/teams`);
  return <GroupMembersClient externalId={who.externalId} />;
}
