import { redirect } from "next/navigation";

export const metadata = { title: "Permissions diagnostics · Astrolift" };

/**
 * Permissions diagnostics moved to the Admin control plane at
 * /administration/permissions. Server-side redirect keeps old
 * deep-links working without a client-side bounce.
 */
export default function SettingsPermissionsAliasPage() {
  redirect("/administration/permissions");
}
