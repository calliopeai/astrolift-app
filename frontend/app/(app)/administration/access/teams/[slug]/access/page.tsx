import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TeamAccessClient } from "../team-clients";

export const metadata = { title: "Access · Team · Astrolift" };

/** A team's Access tab (access UX design 3.2). */
export default async function TeamAccessPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <TeamAccessClient slug={decodeURIComponent(slug)} />
    </PreloadQuery>
  );
}
