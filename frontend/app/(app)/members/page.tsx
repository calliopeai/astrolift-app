import { redirect } from "next/navigation";

export const metadata = { title: "Members · Astrolift" };

/**
 * /administration/members is the canonical org-members surface (it
 * renders the same MembersClient that lives in this directory).
 * Server-side redirect keeps old deep-links working without a
 * client-side bounce.
 */
export default function MembersAliasPage() {
  redirect("/administration/members");
}
