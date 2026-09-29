import { redirect } from "next/navigation";

/** The templates catalog is the Workflows list's Templates view now. */
export default function WorkflowTemplatesPage() {
  redirect("/workflows?view=templates");
}
