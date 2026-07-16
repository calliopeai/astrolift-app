import { redirect } from "next/navigation";

export const metadata = {
  title: "Projects · Astrolift",
};

/**
 * /settings/projects is an alias for the canonical org-projects
 * surface at /administration/projects. Server-side redirect — same
 * reasoning as the sibling members alias.
 */
export default function SettingsProjectsAliasPage() {
  redirect("/administration/projects");
}
