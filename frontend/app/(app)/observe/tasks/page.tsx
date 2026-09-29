import { redirect } from "next/navigation";

// The per-primitive OBSERVE mirror (#892) is the Runs list now, narrowed to
// task runs (spec 44 §4.1). Redirect so old links land.
export default function ObserveTasksPage() {
  redirect("/tasks?kind=task");
}
