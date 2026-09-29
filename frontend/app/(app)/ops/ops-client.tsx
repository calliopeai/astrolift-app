"use client";

import { OpsScreen } from "@/components/screens/ops/OpsScreen";
import { useOps } from "@/components/screens/ops/use-ops";

export function OpsClient() {
  return <OpsScreen {...useOps()} />;
}
