import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("organization");

/**
 * Organization settings moved to the Admin control plane at
 * /administration/organization. Server-side redirect keeps old
 * deep-links working without a client-side bounce.
 */
export default function SettingsOrganizationAliasPage() {
  redirect("/administration/organization");
}
