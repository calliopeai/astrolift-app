"use client";

import { useEffect, useState } from "react";

import { useWorkflowRuns } from "@/graphql/workflows/tiered.hooks";
import type { ConfiguredWorkflowWithRuns } from "@/graphql/workflows/tiered.types";

import { isRunTerminal } from "./workflow-run-state";

// Run-list poll cadence while a run is live — inside the 3–5s window (#1090).
const RUNS_POLL_MS = 4000;

/**
 * Data for a workflow's Observe pillar: the run history (tier-3
 * `workflowRuns`), newest first, and the focused run. The selection lives here
 * because it gates the run-list poll: the list polls while the focused run is
 * non-terminal, which keeps its state live and produces the stop signal for
 * the stage-execution poll; it stops once the run settles.
 */
export function useWorkflowObserve(workflow: ConfiguredWorkflowWithRuns) {
  const { runs, loading, error, refetch, startPolling, stopPolling } = useWorkflowRuns(
    workflow.guid,
    workflow.organizationGuid
  );
  const [selectedGuid, setSelectedGuid] = useState<string | null>(null);

  const sorted = [...runs].sort((a, b) => (a.startedAt < b.startedAt ? 1 : -1));
  // Default the drill-down to the newest run so the live flow shows on load.
  const focusGuid = selectedGuid ?? sorted[0]?.guid ?? null;
  const selected = sorted.find((r) => r.guid === focusGuid) ?? null;
  const focusTerminal = isRunTerminal(selected);

  useEffect(() => {
    if (focusTerminal) {
      stopPolling();
      return;
    }
    startPolling(RUNS_POLL_MS);
    return () => stopPolling();
  }, [focusTerminal, startPolling, stopPolling]);

  return {
    runs: sorted,
    loading,
    error,
    focusGuid,
    selected,
    focusTerminal,
    onSelect: (guid: string | null) => setSelectedGuid(guid),
    onRefresh: () => {
      void refetch();
    },
  };
}

export type WorkflowObserveState = ReturnType<typeof useWorkflowObserve>;
