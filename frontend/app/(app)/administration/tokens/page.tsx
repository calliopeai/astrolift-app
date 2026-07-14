import { redirect } from "next/navigation";

// /tokens is the canonical tokens page (linked from NavUser).
export default function AdministrationTokensPage() {
  redirect("/tokens");
}
