import { redirect } from "next/navigation";

// The live CONTROL page at /clusters owns this surface (#916).
export default function LegacyConnectedClustersPage() {
  redirect("/clusters");
}
