import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("projects");

/**
 * /settings/projects is an alias for the canonical org-projects
 * surface at /administration/projects. Server-side redirect — same
 * reasoning as the sibling members alias.
 */
export default function SettingsProjectsAliasPage() {
  redirect("/administration/projects");
}
