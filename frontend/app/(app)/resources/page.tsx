import { redirect } from "next/navigation";

export default function ResourcesIndexPage() {
  // /resources is a section, not a destination — land operators on
  // Docs which is the default reading surface.
  redirect("/resources/docs");
}
