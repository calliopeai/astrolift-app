import { notFound } from "next/navigation";

import { VizGalleryScreen } from "@/components/screens/dashboard/VizGalleryScreen";
import { isRouteEnabled } from "@/lib/route-flags";

// Dev-only gallery for the #1053 (B1) viz primitives. Not linked in nav;
// reachable at /dev/viz for design review. Safe to remove once the primitives
// are wired across the app.
export default function VizGalleryPage() {
  if (!isRouteEnabled("/dev/viz")) notFound();

  return <VizGalleryScreen />;
}
