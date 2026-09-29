/**
 * Agents › Functions (spec 44 §4.4, §5.1): the org's function workloads,
 * views All · Mine, an app filter, numbered pages. The same
 * `astroliftWorkloadsPage` read as Workloads, held to `kinds: [function]`
 * (#1233, #2155); the server answers every view, chip, search, sort and
 * page. Pure.
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { NumberedListQuery } from "@/components/screens/agents/skills/catalog";
import {
  WORKLOADS_MINE_NOTE,
  workloadsPageVariables,
} from "@/components/screens/workloads/workloads-list";

export const FUNCTIONS_LIST: ListDefinition = {
  id: "agents.functions",
  fields: [
    // Free text: the owning app's slug.
    { key: "app", label: "App" },
  ],
  searchPlaceholder: "Search functions, slugs, apps…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ owner: "me" }, [], { mineNote: WORKLOADS_MINE_NOTE }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** The list state as `astroliftWorkloadsPage` variables, functions only. */
export function functionsPageVariables(q: NumberedListQuery) {
  return workloadsPageVariables(q, ["function"], FUNCTIONS_LIST.defaultSort);
}
