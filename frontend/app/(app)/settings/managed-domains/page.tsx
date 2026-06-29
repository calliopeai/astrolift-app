import { redirect } from "next/navigation";

export const metadata = {
  title: "Managed domains · Astrolift",
};

/**
 * /settings/managed-domains was a duplicate of the canonical
 * Infrastructure → Domains page at /domains — same query, same data
 * (#888). The org-config nav entry and the standalone client are gone;
 * this server-side redirect keeps any bookmarks working.
 */
export default function ManagedDomainsAliasPage() {
  redirect("/domains");
}
