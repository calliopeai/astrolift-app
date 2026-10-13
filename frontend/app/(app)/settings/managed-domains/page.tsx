import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("managedDomains");

/**
 * /settings/managed-domains was a duplicate of the canonical
 * Infrastructure → Domains page at /domains — same query, same data
 * (#888). The org-config nav entry and the standalone client are gone;
 * this server-side redirect keeps any bookmarks working.
 */
export default function ManagedDomainsAliasPage() {
  redirect("/domains");
}
