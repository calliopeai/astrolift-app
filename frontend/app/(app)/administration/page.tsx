import { redirect } from "next/navigation";

export default function AdministrationIndexPage() {
  // Land on the first entry of the Admin subnav's Organization group.
  redirect("/administration/organization");
}
