/**
 * Agents › Workloads (spec 44 §4.1, §10.2): the runtime units behind agents,
 * workflows and functions in one list, views All · Mine · Agents ·
 * Workflows · Functions, an app filter, numbered pages. An app's own
 * deployment, statefulset, job and cronjob workloads stay on that app's
 * Workloads tab, so this list never holds them.
 *
 * The server answers every view, chip, search, sort and page
 * (`astroliftWorkloadsPage` with `kinds` held to the area's three, #2155):
 * Mine is the workloads the viewer owns (`owner: "me"`). Nothing is
 * filtered, sorted or paged in the browser. Pure.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import {
  type NumberedListQuery,
  numberedPageVariables,
} from "@/components/screens/agents/skills/catalog";
import type { AstroliftWorkload, WorkloadKind } from "@/graphql/registry/registry.types";

/** The kinds the Agents area owns (§10.2). */
export const AREA_KINDS: readonly WorkloadKind[] = ["agent", "workflow", "function"];

export const KIND_LABEL: Record<string, string> = {
  agent: "Agent",
  workflow: "Workflow",
  function: "Function",
};

/** `2`, `1–4` (autoscaled), or `0–4` for a function that scales to zero. */
export function scaleLabel(w: AstroliftWorkload): string {
  if (w.hpaMaxReplicas != null) return `${w.hpaMinReplicas ?? 0}–${w.hpaMaxReplicas}`;
  return String(w.replicas ?? 0);
}

const MINE_NOTE =
  "Mine means workloads you created, or on apps you created. Workloads from before Astrolift recorded who created them show only in All.";

export const WORKLOADS_LIST: ListDefinition = {
  id: "agents.workloads",
  fields: [
    // Free text: the owning app's slug.
    { key: "app", label: "App" },
  ],
  // The server matches name, slug and the owning app.
  searchPlaceholder: "Search workloads, slugs, apps…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews(
    { owner: "me" },
    [
      { key: "agents", label: "Agents", filters: { kind: "agent" } },
      { key: "workflows", label: "Workflows", filters: { kind: "workflow" } },
      { key: "functions", label: "Functions", filters: { kind: "function" } },
    ],
    { mineNote: MINE_NOTE }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** The Mine view's note, which Functions shares. */
export const WORKLOADS_MINE_NOTE = MINE_NOTE;

export interface WorkloadsFilter {
  kind?: string[];
  app?: string[];
  owner?: string[];
}

/**
 * The list state as `astroliftWorkloadsPage` variables: `kinds` holds the
 * list to the Agents area's kinds (or fewer, for Functions), and the view's
 * kind, the app chip and Mine are the `filter`. `sort` is always sent, which
 * selects numbered paging.
 */
export function workloadsPageVariables(
  q: NumberedListQuery,
  kinds: readonly WorkloadKind[] = AREA_KINDS,
  defaultSort = WORKLOADS_LIST.defaultSort
) {
  const f = q.filters;
  const filter: WorkloadsFilter = {};
  if (f.kind) filter.kind = [f.kind];
  if (f.app) filter.app = [f.app];
  if (f.owner) filter.owner = [f.owner];
  return { kinds: [...kinds], ...numberedPageVariables(q, filter, defaultSort) };
}
