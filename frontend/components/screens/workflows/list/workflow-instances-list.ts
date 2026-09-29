/**
 * Agents › Workflows › Platform instances as a list (spec 44 §5.1): the
 * Temporal instances behind deploys, provisioning and drift detection, with
 * views, type and status filters, search, sort and numbered pages. Pure.
 *
 * `astroliftWorkflowInstances` takes `workflowType` (exact) and `status`
 * and pages by Temporal's own cursor, newest first, but takes no search or
 * sort. So Type and Status go to the server, the hook reads one page at the
 * backend's cap (`INSTANCES_LIMIT`), and search, sort and paging run over
 * it in the browser (selectRows), which every view's note says. When the
 * field takes `search`, `sort` and `page` the hook sends them instead and
 * the screen does not change.
 */
import { type ListDefinition, type ListView, standardViews } from "@/components/list/list-state";
import {
  selectRows,
  type SelectRowsSpec,
  type SelectRowsState,
} from "@/components/list/select-rows";
import type { WorkflowInstance } from "@/graphql/workflows/workflows.types";

/** The backend's page cap for Temporal instances: the list reads this many. */
export const INSTANCES_LIMIT = 200;

/** Temporal execution statuses, as the URL spells them; the query takes them upper-cased. */
export const INSTANCE_STATUS_LABEL: Record<string, string> = {
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  canceled: "Cancelled",
  terminated: "Terminated",
  timed_out: "Timed out",
};

const BROWSER_NOTE = `Search, sort and pages cover the newest ${INSTANCES_LIMIT} matching instances; Temporal lists newest first and does not search or sort.`;

function withNote(views: ListView[]): ListView[] {
  return views.map((v) => ({ ...v, note: v.note ? `${v.note} ${BROWSER_NOTE}` : BROWSER_NOTE }));
}

export const WORKFLOW_INSTANCES_LIST: ListDefinition = {
  id: "workflows.instances",
  fields: [
    // Free text, matched exactly by Temporal: `DeployAppWorkflow`.
    { key: "type", label: "Type" },
    {
      key: "status",
      label: "Status",
      options: Object.entries(INSTANCE_STATUS_LABEL).map(([value, label]) => ({
        value,
        label: label.toLowerCase(),
      })),
    },
  ],
  searchPlaceholder: "Search instance ids, types, who started them…",
  defaultSort: [{ key: "started", dir: "desc" }],
  views: withNote(
    standardViews(
      { mine: "1" },
      [
        { key: "running", label: "Running", filters: { status: "running" } },
        { key: "failed", label: "Failed", filters: { status: "failed" } },
      ],
      {
        mineNote:
          "Instances record who started them as a name, not an account, so Mine is empty until they record the account.",
      }
    )
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** The query's variables for the list's filters: Type and Status are the server's. */
export function instancesVariables(filters: Record<string, string>) {
  return {
    workflowType: filters.type?.trim() || null,
    status: filters.status ? filters.status.toUpperCase() : null,
    limit: INSTANCES_LIMIT,
  };
}

/** A row's key: a workflow id can recur across runs. */
export const instanceKey = (i: WorkflowInstance) => `${i.workflowId}:${i.runId}`;

const SPEC: SelectRowsSpec<WorkflowInstance> = {
  filter: {
    type: (i, v) => i.workflowType === v.trim(),
    status: (i, v) => i.status.toLowerCase() === v.toLowerCase(),
    // The actor is a display label, not the viewer's account.
    mine: () => false,
  },
  text: (i) => [i.workflowId, i.workflowType, i.runId, i.triggeredBy, i.taskQueue],
  sort: {
    started: (i) => (i.startedAt ? Date.parse(i.startedAt) : 0),
    type: (i) => i.workflowType.toLowerCase(),
    status: (i) => i.status,
    duration: (i) => i.durationSeconds ?? -1,
  },
  id: instanceKey,
};

/** One numbered page of the instances matching the view, chips and search. */
export function selectInstances(
  rows: readonly WorkflowInstance[],
  state: SelectRowsState
): { rows: WorkflowInstance[]; totalCount: number } {
  return selectRows(rows, state, SPEC);
}

/** `42.3s`, `12m 34s`, `2h 1m`. */
export function formatInstanceDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
