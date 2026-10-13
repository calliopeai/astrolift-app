import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("policies");

/**
 * Policies moved to the Admin control plane at
 * /administration/policies. Server-side redirect keeps old
 * deep-links working without a client-side bounce.
 */
export default function SettingsPoliciesAliasPage() {
  redirect("/administration/policies");
}
