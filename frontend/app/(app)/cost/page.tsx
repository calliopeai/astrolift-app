import { redirect } from "next/navigation";

// /administration/cost is the canonical cost page.
export default function CostPage() {
  redirect("/administration/cost");
}
