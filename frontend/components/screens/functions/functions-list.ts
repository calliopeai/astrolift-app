/**
 * Agents › Functions (spec 44 §4.4, §5.1): the org's function workloads,
 * views All · Mine, an app filter, numbered pages. The same walk and step as
 * Workloads, narrowed to `kind: function` (#1233), so the two lists read one
 * cache entry. Mine lists every function until workloads record who owns
 * them. Pure.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import { clientNote, withAllNote } from "@/components/screens/agents/skills/catalog";
import { selectWorkloads } from "@/components/screens/workloads/workloads-list";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

const NOTE = clientNote("the workload field has no kind or page argument, so every page is read");

export const FUNCTIONS_LIST: ListDefinition = {
  id: "agents.functions",
  fields: [
    // Free text: the owning app's slug.
    { key: "app", label: "App" },
  ],
  searchPlaceholder: "Search functions, slugs, apps…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: withAllNote(
    standardViews({}, [], {
      mineNote: `Mine lists every function until workloads record who owns them. ${NOTE}`,
    }),
    NOTE
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function selectFunctions(
  workloads: AstroliftWorkload[],
  q: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  return selectWorkloads(workloads, { ...q, filters: { ...q.filters, kind: "function" } });
}
