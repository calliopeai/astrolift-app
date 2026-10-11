import { PersonTeamsClient } from "../principal-clients";

export const metadata = { title: "Teams · Person · Astrolift" };

export default async function PersonTeamsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PersonTeamsClient memberId={id} />;
}
