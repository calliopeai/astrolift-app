import { redirect } from "next/navigation";

// The per-primitive OBSERVE mirror was folded into the primitive's own page
// (#892); Functions is one list at /functions now. Redirect so old links land.
export default function ObserveFunctionsPage() {
  redirect("/functions");
}
