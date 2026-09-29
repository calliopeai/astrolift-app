"use client";

import { RunsScreen } from "@/components/screens/tasks/RunsScreen";
import { useRuns } from "@/components/screens/tasks/use-runs";

/** Agents › Runs: the hook merges the run sources, the screen draws the list. */
export function TasksClient() {
  return <RunsScreen {...useRuns()} />;
}
