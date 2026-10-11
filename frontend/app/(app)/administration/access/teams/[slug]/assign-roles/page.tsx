import { LegacyTeamAssignmentsClient } from "@/components/screens/administration/access/team-memberships/LegacyTeamAssignmentsClient";
export const metadata = { title: "Assign roles · Team · Astrolift" };
export default async function LegacyAssignmentsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <LegacyTeamAssignmentsClient slug={decodeURIComponent(slug)} />;
}
