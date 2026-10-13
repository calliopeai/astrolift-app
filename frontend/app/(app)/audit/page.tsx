import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("audit");

/**
 * Audit moved to the Admin control plane at /administration/audit.
 * Server-side redirect keeps old deep-links working without a
 * client-side bounce.
 */
export default function AuditAliasPage() {
  redirect("/administration/audit");
}
