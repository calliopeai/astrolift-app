import { TeamMembersClient } from "../team-clients";

export const metadata = { title: "Members · Team · Astrolift" };

/** The scoped membership client resolves one exact team, without capped SSR lists. */
export default async function TeamMembersPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <TeamMembersClient slug={decodeURIComponent(slug)} />;
}
