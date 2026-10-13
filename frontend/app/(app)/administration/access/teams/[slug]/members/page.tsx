import { routeTitle } from "@/lib/i18n/route-title";

import { TeamMembersClient } from "../team-clients";

export const generateMetadata = routeTitle("members", "team");

/** The scoped membership client resolves one exact team, without capped SSR lists. */
export default async function TeamMembersPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <TeamMembersClient slug={decodeURIComponent(slug)} />;
}
