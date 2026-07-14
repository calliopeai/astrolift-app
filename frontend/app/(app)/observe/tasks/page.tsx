import { redirect } from "next/navigation";

// The per-primitive OBSERVE mirror was folded into the primitive's own page
// (#892) — signal tabs live at /tasks now. Redirect so old links land.
export default function ObserveTasksPage() {
  redirect("/tasks");
}
