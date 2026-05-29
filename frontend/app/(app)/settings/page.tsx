import { redirect } from "next/navigation";

export default function SettingsIndexPage() {
  // /settings is a section, not a destination — land on Organization
  // which is the primary org-level settings surface.
  redirect("/settings/organization");
}
