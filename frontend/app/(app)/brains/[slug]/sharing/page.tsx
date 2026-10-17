import { routeTitle } from "@/lib/i18n/route-title";

import { SharingContent } from "../components/sharing-content";

export const generateMetadata = routeTitle("sharing", "semanticLayer");

/** Sharing: teams as viewer or editor, and the access history, by `?section=`. */
export default function BrainSharingPage() {
  return <SharingContent />;
}
