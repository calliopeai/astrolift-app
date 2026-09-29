"use client";

import { FleetMapPanel } from "@/components/screens/fleet/FleetMapPanel";
import { useFleetMap } from "@/components/screens/fleet/use-fleet-map";

/** /fleet/map client (#1091 — LiveFlowMap P2): polls the fleet and renders the live map. */
export function FleetMapClient() {
  return <FleetMapPanel {...useFleetMap()} />;
}
