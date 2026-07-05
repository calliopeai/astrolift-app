import { redirect } from "next/navigation";

// The static /resources/docs link directory is retired in favour of the real
// in-app /documentation hub (#894); its curated GitHub references were migrated
// into the hub's "Reference (on GitHub)" section. Redirect so old links land.
export default function LegacyResourcesDocsPage() {
  redirect("/documentation");
}
