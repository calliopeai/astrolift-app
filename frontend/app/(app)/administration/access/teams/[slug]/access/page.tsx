import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { TeamAccessClient } from "../team-clients";

export const generateMetadata = routeTitle("access", "team");

/** A team's Access tab (access UX design 3.2). */
export default async function TeamAccessPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <TeamAccessClient slug={decodeURIComponent(slug)} />
    </PreloadQuery>
  );
}
