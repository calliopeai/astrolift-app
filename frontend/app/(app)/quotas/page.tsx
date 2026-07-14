import { redirect } from "next/navigation";

// /administration/quotas is the canonical quotas page.
export default function QuotasPage() {
  redirect("/administration/quotas");
}
