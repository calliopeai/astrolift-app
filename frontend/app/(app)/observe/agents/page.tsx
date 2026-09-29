import { redirect } from "next/navigation";

// The per-primitive OBSERVE mirror (#892) is the Runs list now, narrowed to
// agent runs (spec 44 §4.1). Redirect so old links land.
export default function ObserveAgentsPage() {
  redirect("/tasks?kind=agent");
}
