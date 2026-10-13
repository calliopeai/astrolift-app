import { routeTitle } from "@/lib/i18n/route-title";

import { PersonTeamsClient } from "../principal-clients";

export const generateMetadata = routeTitle("teams", "person");

export default async function PersonTeamsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PersonTeamsClient memberId={id} />;
}
