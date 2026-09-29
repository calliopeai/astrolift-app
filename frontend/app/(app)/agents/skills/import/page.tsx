import { redirect } from "next/navigation";

// Import from repo is a sheet over the Skills list now (spec 44 §5.4: two
// fields). Redirect so old links open it.
export default function ImportSkillsPage() {
  redirect("/agents/skills?import=1");
}
