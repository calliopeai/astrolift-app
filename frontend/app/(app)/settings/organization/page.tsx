import { redirect } from "next/navigation";

export const metadata = { title: "Organization · Astrolift" };

/**
 * Organization settings moved to the Admin control plane at
 * /administration/organization. Server-side redirect keeps old
 * deep-links working without a client-side bounce.
 */
export default function SettingsOrganizationAliasPage() {
  redirect("/administration/organization");
}
