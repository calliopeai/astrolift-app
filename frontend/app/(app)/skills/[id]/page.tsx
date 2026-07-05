import { redirect } from "next/navigation";

// Legacy /skills/[id] → the canonical per-skill detail under /agents (#898).
export default async function LegacySkillDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/agents/skills/${id}`);
}
