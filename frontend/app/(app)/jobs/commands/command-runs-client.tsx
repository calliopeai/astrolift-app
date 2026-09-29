"use client";

import { CommandRunsScreen } from "@/components/screens/jobs/CommandRunsScreen";
import { useCommandRuns } from "@/components/screens/jobs/use-command-runs";

export function CommandRunsClient() {
  return <CommandRunsScreen {...useCommandRuns()} />;
}
