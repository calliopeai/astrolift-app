import { routeTitle } from "@/lib/i18n/route-title";

import { ZentinelleClient } from "./zentinelle-client";

export const generateMetadata = routeTitle("zentinelle", "secure");

export default function ZentinellePage() {
  return <ZentinelleClient />;
}
