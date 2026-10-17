import { routeTitle } from "@/lib/i18n/route-title";

import { SubstrateContent } from "../components/substrate-content";

export const generateMetadata = routeTitle("substrate", "semanticLayer");

/** Substrate: Mimwell's cell, its resources, and graduation when Mimwell asks for it. */
export default function BrainSubstratePage() {
  return <SubstrateContent />;
}
