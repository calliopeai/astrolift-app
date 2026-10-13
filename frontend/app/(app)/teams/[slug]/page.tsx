import { redirect } from "next/navigation";

import { TEAMS_HREF } from "@/components/screens/administration/access/access-nav";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("team");

/** A team's page moved to Admin › Access › Teams (access UX design 5). */
export default async function TeamDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  redirect(`${TEAMS_HREF}/${slug}`);
}
