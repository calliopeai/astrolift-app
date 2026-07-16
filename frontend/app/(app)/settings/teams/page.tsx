import { redirect } from "next/navigation";

export const metadata = {
  title: "Teams · Astrolift",
};

/**
 * /settings/teams is an alias for the canonical org-teams surface
 * at /administration/teams. Server-side redirect — same reasoning as
 * the sibling members alias.
 */
export default function SettingsTeamsAliasPage() {
  redirect("/administration/teams");
}
