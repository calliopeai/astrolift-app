import { redirect } from "next/navigation";

// The per-primitive OBSERVE mirror was folded into the primitive's own page
// (#892) — signal tabs live at /agents now. Redirect so old links land.
export default function ObserveAgentsPage() {
  redirect("/agents");
}
