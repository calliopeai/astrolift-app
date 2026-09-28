"use client";

import { FleetOverviewScreen } from "@/components/screens/fleet/FleetOverviewScreen";
import { useFleetOverview } from "@/components/screens/fleet/use-fleet-overview";

import { FleetMapClient } from "./map/fleet-map-client";

export function FleetOverviewClient() {
  return <FleetOverviewScreen {...useFleetOverview()} map={<FleetMapClient />} />;
}
