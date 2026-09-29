/**
 * A workflow's Triggers tab as a list (spec 44 §5.1, §5.2): every way the
 * workflow starts, one row each, with views, kind and status filters, sort
 * and numbered pages. Pure.
 *
 * The rows come from what the tab already reads: a configured workflow
 * carries one trigger (`triggerKind`, `scheduleCron`, `isEnabled`), so
 * today the list holds one row, and none for a definition opened directly.
 * Enabled is the workflow's own switch, so each row's toggle turns the
 * workflow on or off. When a workflow can hold several triggers the hook
 * reads them from a triggers query and this declaration does not change.
 * Filter, search, sort and paging run in the browser (selectRows) until
 * that query takes the list's arguments.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import {
  selectRows,
  type SelectRowsSpec,
  type SelectRowsState,
} from "@/components/list/select-rows";
import type { ConfiguredWorkflow } from "@/graphql/workflows/tiered.types";

import { formatTriggerKind } from "./workflow-run-state";

/** The kinds a configured workflow's trigger takes; any other value lists as itself. */
export const TRIGGER_KINDS = ["schedule", "webhook", "manual"] as const;

export const WORKFLOW_TRIGGERS_LIST: ListDefinition = {
  id: "workflows.workflow-triggers",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: TRIGGER_KINDS.map((value) => ({ value, label: formatTriggerKind(value) })),
    },
    {
      key: "status",
      label: "Status",
      options: [
        { value: "enabled", label: "enabled" },
        { value: "disabled", label: "disabled" },
      ],
    },
  ],
  searchPlaceholder: "Search triggers, schedules…",
  defaultSort: [{ key: "kind", dir: "asc" }],
  // All says what the list can hold today; the rest are the standard views.
  views: standardViews(
    { mine: "1" },
    [
      { key: "enabled", label: "Enabled", filters: { status: "enabled" } },
      { key: "disabled", label: "Disabled", filters: { status: "disabled" } },
    ],
    { mineNote: "Triggers do not record who set them up yet, so Mine is empty until they do." }
  ).map((v) =>
    v.key === "all"
      ? { ...v, note: "A workflow holds one trigger today, so this list shows that one." }
      : v
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** One way the workflow starts. */
export interface TriggerRow {
  id: string;
  /** `schedule`, `webhook`, `manual`, or whatever the backend sends. */
  kind: string;
  /** The cron expression for a schedule; null otherwise. */
  schedule: string | null;
  isEnabled: boolean;
}

/** The workflow's triggers as rows: its one trigger, or none for a definition. */
export function triggerRows(
  workflow: Pick<ConfiguredWorkflow, "slug" | "triggerKind" | "scheduleCron" | "isEnabled"> | null
): TriggerRow[] {
  if (!workflow) return [];
  return [
    {
      id: `${workflow.slug}:${workflow.triggerKind}`,
      kind: workflow.triggerKind.toLowerCase(),
      schedule: workflow.scheduleCron,
      isEnabled: workflow.isEnabled,
    },
  ];
}

const status = (r: TriggerRow) => (r.isEnabled ? "enabled" : "disabled");

const SPEC: SelectRowsSpec<TriggerRow> = {
  filter: {
    kind: (r, v) => r.kind === v.toLowerCase(),
    status: (r, v) => status(r) === v.toLowerCase(),
    // No trigger records who set it up yet.
    mine: () => false,
  },
  text: (r) => [r.kind, formatTriggerKind(r.kind), r.schedule],
  sort: {
    kind: (r) => formatTriggerKind(r.kind).toLowerCase(),
    status: status,
  },
  id: (r) => r.id,
};

/** One numbered page of the triggers matching the view, chips and search. */
export function selectTriggers(
  rows: readonly TriggerRow[],
  state: SelectRowsState
): { rows: TriggerRow[]; totalCount: number } {
  return selectRows(rows, state, SPEC);
}
