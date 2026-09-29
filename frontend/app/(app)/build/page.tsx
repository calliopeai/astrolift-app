import { redirect } from "next/navigation";

/**
 * The old Build pillar gateway (a "coming soon" page, linked from nowhere
 * since navigation went by function, spec 44 §4.1). Home is where an old
 * link lands.
 */
export default function BuildPage() {
  redirect("/dashboard");
}
