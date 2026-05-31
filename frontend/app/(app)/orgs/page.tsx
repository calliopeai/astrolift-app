import { redirect } from "next/navigation";

// /orgs has no standalone page — org settings live at /settings/organization.
export default function OrgsPage() {
  redirect("/settings/organization");
}
