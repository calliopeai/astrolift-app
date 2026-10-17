"use client";

import { ZentinelleScreen } from "@/components/screens/secure/ZentinelleScreen";
import { useZentinelle } from "@/components/screens/secure/use-zentinelle";

/** Secure › Zentinelle, wired to the integration's reads and writes (#1888). */
export function ZentinelleClient() {
  return <ZentinelleScreen {...useZentinelle()} />;
}
