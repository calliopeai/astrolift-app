import { redirect } from "next/navigation";

// Moved into the documentation hub (#916).
export default function LegacyHelpPage() {
  redirect("/documentation/help");
}
