import { redirect } from "next/navigation";

export default function ResourcesIndexPage() {
  // /resources is a section, not a destination. Docs moved to the canonical
  // /documentation hub (#894); land on the manifest reference — the first of
  // the remaining in-section reference tabs.
  redirect("/resources/manifest");
}
