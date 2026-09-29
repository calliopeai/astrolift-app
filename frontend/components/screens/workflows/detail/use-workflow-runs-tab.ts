"use client";

import * as React from "react";

import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import {
  filterRunRows,
  fromWorkflowRun,
  pageRunRows,
  type RunRow,
  sortRunRows,
} from "@/components/screens/tasks/runs-list";
import { useWorkflowDefinitionRuns, useWorkflowRuns } from "@/graphql/workflows/tiered.hooks";

import type { FramedWorkflow } from "./use-workflow-frame";
import {
  configuredRunSubject,
  definitionRunSubject,
  liveRunsSnapshot,
  type WorkflowRunSubject,
} from "./workflow-run-model";
import { fromConfiguredRun, WORKFLOW_RUNS_LIST } from "./workflow-runs-list";
import type { WorkflowRunsTabProps } from "./WorkflowRunsTab";

/** Poll fast while a run is live on the first page, slowly otherwise, never on older pages. */
const LIVE_POLL_MS = 4000;
const IDLE_POLL_MS = 15_000;
/** The same window the frame reads a definition's runs with, so Apollo serves it once. */
const DEFINITION_RUNS_LIMIT = 100;

/**
 * The Runs tab's data half. A configured workflow's runs are its tier-3
 * `workflowRuns`; a definition's are `workflowDefinitionRuns` for its
 * project, narrowed to it, the query and variables the frame already reads
 * for the header. Either way the rows are the shared Runs list's, filtered,
 * sorted and paged in the browser (CLIENT-SIDE until a paged per-workflow
 * runs query exists). The live view above the list is the definition's
 * line with the runs the engine has placed on it; a configured run reports
 * no current stage, so it gets no live view.
 */
export function useWorkflowRunsTab(framed: FramedWorkflow): WorkflowRunsTabProps {
  const list = useListState(WORKFLOW_RUNS_LIST);
  const [now] = React.useState(() => Date.now());
  const firstPage = list.state.after === null;
  const configured = framed.kind === "configured" ? framed.workflow : null;
  const definition = framed.kind === "definition" ? framed.definition : null;

  const configuredQ = useWorkflowRuns(
    configured?.guid ?? null,
    configured?.organizationGuid ?? null
  );
  const definitionQ = useWorkflowDefinitionRuns({
    orgId: definition?.organizationGuid,
    projectId: definition?.projectGuid,
    limit: DEFINITION_RUNS_LIMIT,
    skip: !definition,
  });
  const q = configured ? configuredQ : definitionQ;
  const definitionRuns = definitionQ.runs.filter((r) => r.definitionGuid === definition?.guid);

  const subjects: WorkflowRunSubject[] = configured
    ? configuredQ.runs.map(configuredRunSubject)
    : definitionRuns.map(definitionRunSubject);
  const anyLive = subjects.some((s) => s.live);

  const { startPolling, stopPolling } = q;
  React.useEffect(() => {
    if (!firstPage) {
      stopPolling();
      return;
    }
    startPolling(anyLive ? LIVE_POLL_MS : IDLE_POLL_MS);
    return () => stopPolling();
  }, [firstPage, anyLive, startPolling, stopPolling]);

  const merged: RunRow[] = configured
    ? configuredQ.runs.map((r) => fromConfiguredRun(r, configured))
    : definitionRuns.map(fromWorkflowRun);
  const filtered = sortRunRows(
    filterRunRows(merged, list.filters, list.state.q, now),
    list.state.sort
  );
  const page = pageRunRows(filtered, list.state.after, list.state.pageSize);
  const pending = q.loading && merged.length === 0;

  const held = useHeldRows(page.rows, (r) => r.key, {
    live: firstPage && !pending,
    resetKey:
      JSON.stringify(list.filters) +
      list.state.q +
      list.state.pageSize +
      JSON.stringify(list.state.sort),
  });

  const capped = Boolean(definition) && definitionQ.runs.length >= DEFINITION_RUNS_LIMIT;

  return {
    list,
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: pending,
    stale: q.loading && !pending,
    error: q.error && merged.length === 0 ? { message: q.error.message } : null,
    onRetry: () => void q.refetch(),
    nextCursor: page.nextCursor,
    totalCount: pending ? null : filtered.length,
    approximateCount: capped,
    coverage: capped
      ? `Searched the newest ${DEFINITION_RUNS_LIMIT} runs in this workflow's project; filters and search cover those.`
      : null,
    live: definition
      ? liveRunsSnapshot(definition.stages, subjects, {
          lineId: definition.guid,
          name: definition.name,
          now,
        })
      : null,
  };
}
