import { type ListDefinition, type ListView, standardViews } from "@/components/list/list-state";
import type { SelectRowsState } from "@/components/list/select-rows";
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

const PAGE_NOTE =
  "Temporal pages newest first. Type and Status filter on the server. Empty pages may have more authorized instances on Older pages.";

function withNote(views: ListView[]): ListView[] {
  return views.map((v) => ({ ...v, note: v.note ? `${v.note} ${PAGE_NOTE}` : PAGE_NOTE }));
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
  searchPlaceholder: "Search instances",
  searchable: false,
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
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** The query's variables for the list's filters: Type and Status are the server's. */
export function instancesVariables(
  filters: Record<string, string>,
  pageSize = 25,
  after: string | null = null
) {
  return {
    workflowType: filters.type?.trim() || null,
    status: filters.status ? filters.status.toUpperCase() : null,
    limit: Math.min(INSTANCES_LIMIT, Math.max(1, pageSize)),
    after,
  };
}

/** A row's key: a workflow id can recur across runs. */
export const instanceKey = (i: WorkflowInstance) => `${i.workflowId}:${i.runId}`;

/** Temporal supplies ordering and page membership; never search or slice a partial page. */
export function selectInstances(
  rows: readonly WorkflowInstance[],
  state: SelectRowsState
): { rows: WorkflowInstance[]; totalCount: number } {
  const selected = state.filters.mine ? [] : [...rows];
  return { rows: selected, totalCount: selected.length };
}

export function formatInstanceDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
