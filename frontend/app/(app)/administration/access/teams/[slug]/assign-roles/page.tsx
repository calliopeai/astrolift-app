import { LegacyTeamAssignmentsClient } from "@/components/screens/administration/access/team-memberships/LegacyTeamAssignmentsClient";
import { routeTitle } from "@/lib/i18n/route-title";
export const generateMetadata = routeTitle("assignRoles", "team");
export default async function LegacyAssignmentsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <LegacyTeamAssignmentsClient slug={decodeURIComponent(slug)} />;
}
