import { redirect } from "next/navigation";

// /orgs has no standalone page — org settings live at
// /administration/organization.
export default function OrgsPage() {
  redirect("/administration/organization");
}
