import { redirect } from "next/navigation";

// /resources is retired (#916) — its references live in the /documentation
// hub now. Redirect straight to the hub so old links land.
export default function ResourcesIndexPage() {
  redirect("/documentation");
}
