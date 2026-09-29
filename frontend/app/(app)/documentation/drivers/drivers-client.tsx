"use client";

import { DriversScreen } from "@/components/screens/documentation/DriversScreen";
import { useDrivers } from "@/components/screens/documentation/use-drivers";

export function DriversClient() {
  const drivers = useDrivers();
  return <DriversScreen {...drivers} />;
}
