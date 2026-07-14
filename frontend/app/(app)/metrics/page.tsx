import { redirect } from "next/navigation";

// /administration/metrics is the canonical metrics page.
export default function MetricsPage() {
  redirect("/administration/metrics");
}
