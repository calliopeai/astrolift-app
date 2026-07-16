import { redirect } from "next/navigation";

export const metadata = { title: "Audit · Astrolift" };

/**
 * Audit moved to the Admin control plane at /administration/audit.
 * Server-side redirect keeps old deep-links working without a
 * client-side bounce.
 */
export default function AuditAliasPage() {
  redirect("/administration/audit");
}
