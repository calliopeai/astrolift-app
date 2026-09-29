"use client";

import { useWorkloadsList } from "@/components/screens/workloads/use-workloads-list";
import { WorkloadsScreen } from "@/components/screens/workloads/WorkloadsScreen";

/** The Workloads page: the agent, workflow and function workloads on the shared list. */
export function WorkloadsClient() {
  return <WorkloadsScreen {...useWorkloadsList()} />;
}
