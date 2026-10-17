"use client";

import { BrainAppsTab } from "@/components/screens/brains/detail/BrainAppsTab";
import { useBrainApps } from "@/components/screens/brains/detail/use-brain-apps";

import { useFramedBrain } from "./framed-brain";

export function AppsContent() {
  return <BrainAppsTab {...useBrainApps(useFramedBrain())} />;
}
