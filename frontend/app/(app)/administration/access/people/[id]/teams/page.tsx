import { redirect } from "next/navigation";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";
import { parsePrincipalParam } from "@/components/screens/administration/access/principal-tabs";

import { PersonTeamsClient } from "../principal-clients";

export const metadata = { title: "Teams · People · Astrolift" };

/** A person's Teams tab. A group has Members instead. */
export default async function PersonTeamsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const who = parsePrincipalParam(id);
  if (who.kind === "group") redirect(`${PEOPLE_HREF}/${id}/members`);
  return <PersonTeamsClient memberId={who.memberId} />;
}
