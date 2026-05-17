import { LIST_ROLES, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TeamDetailClient } from "./team-detail-client";

export const metadata = {
  title: "Team · Astrolift",
};

export default async function TeamDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <PreloadQuery query={LIST_ROLES}>
        <TeamDetailClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
