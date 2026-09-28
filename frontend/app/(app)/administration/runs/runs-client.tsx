"use client";

import { CombinedRunsScreen } from "@/components/screens/administration/insights/CombinedRunsScreen";
import { useCombinedRuns } from "@/components/screens/administration/insights/use-combined-runs";

export function RunsClient() {
  return <CombinedRunsScreen {...useCombinedRuns()} />;
}
