import { redirect } from "next/navigation";

export default function SettingsIndexPage() {
  // /settings is a section, not a destination — org-level settings
  // live in the Admin control plane; land on Organization there.
  redirect("/administration/organization");
}
