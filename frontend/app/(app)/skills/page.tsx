import { redirect } from "next/navigation";

// Legacy top-level /skills is a stale duplicate of /agents/skills (#898).
// Redirect rather than 404 so old bookmarks still land.
export default function LegacySkillsPage() {
  redirect("/agents/skills");
}
