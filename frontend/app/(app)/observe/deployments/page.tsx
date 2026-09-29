import { redirect } from "next/navigation";

// The per-primitive OBSERVE mirror duplicated the deployments list (#892);
// Apps › Deployments is the one list now. Redirect so old links land.
export default function ObserveDeploymentsPage() {
  redirect("/deployments");
}
