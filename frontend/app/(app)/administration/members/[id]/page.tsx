import { redirect } from "next/navigation";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("member");

/** A member's page moved to Admin › Access › People (access UX design 5). */
export default async function MemberDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`${PEOPLE_HREF}/${id}`);
}
