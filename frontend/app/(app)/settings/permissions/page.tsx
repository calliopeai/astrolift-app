import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("permissionsDiagnostics");

/**
 * Permissions diagnostics moved to the Admin control plane. This alias
 * landed on Roles, which is not what its links asked for; it goes to
 * Diagnostics, the page that answers "what can I do and why". Server-side,
 * so old deep-links work without a client-side bounce.
 */
export default function SettingsPermissionsAliasPage() {
  redirect("/administration/permissions/diagnostics");
}
