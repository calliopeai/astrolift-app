import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TeamMembersClient } from "../team-clients";

export const metadata = { title: "Members · Team · Astrolift" };

/** A team's Members tab. */
export default async function TeamMembersPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <TeamMembersClient slug={decodeURIComponent(slug)} />
    </PreloadQuery>
  );
}
