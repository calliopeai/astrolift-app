import { redirect } from "next/navigation";

// Legacy /skills/new → the canonical agent skills surface (#898).
export default function LegacyNewSkillPage() {
  redirect("/agents/skills/new");
}
