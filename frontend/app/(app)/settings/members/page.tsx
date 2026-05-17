import { redirect } from "next/navigation";

export const metadata = {
  title: "Members · Astrolift",
};

/**
 * /settings/members is an alias for the canonical org-members
 * surface at /members (#413). The redirect runs server-side so
 * deep-links from a settings-shaped mental model land on the real
 * page without a client-side bounce. Permission gating lives on
 * /members and is unchanged by this alias.
 */
export default function SettingsMembersAliasPage() {
  redirect("/members");
}
