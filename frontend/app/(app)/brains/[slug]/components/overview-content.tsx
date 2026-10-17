"use client";

import { BrainOverviewTab } from "@/components/screens/brains/detail/BrainOverviewTab";
import { useBrainOverview } from "@/components/screens/brains/detail/use-brain-overview";

import { useFramedBrain } from "./framed-brain";

export function OverviewContent() {
  return <BrainOverviewTab {...useBrainOverview(useFramedBrain())} />;
}
