/**
 * Apps › Semantic layers (#2320, #2427): the brain apps the viewer can read.
 * `astroliftBrainsPage` (#2444) answers the search, the home team, the order
 * and the cursor on the server, and lists archived layers in the "With
 * archived" view so Restore is a row away. Pure.
 */
import type { SortState } from "@/components/data-table";
import type { CopiedListDefinition, ListDefinition } from "@/components/list/list-state";
import type {
  AstroliftBrainSort,
  ListBrainsPageQuery,
  ListBrainsPageQueryVariables,
} from "@/graphql/__generated__/operations";

export type BrainRow = ListBrainsPageQuery["astroliftBrainsPage"]["items"][number];

export const BRAINS_LIST: CopiedListDefinition = {
  id: "apps.brains",
  copy: "brains",
  // Options are the teams the viewer can see (`withTeamOptions`).
  fields: [{ key: "team", label: "Team" }],
  searchPlaceholder: "Search names and slugs…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: [
    { key: "all", label: "All", filters: {} },
    { key: "archived", label: "With archived", filters: { archived: "1" } },
  ],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** The server's order for the list's sort: by name either way, else newest first. */
export function brainSort(sort: readonly SortState[]): AstroliftBrainSort {
  const first = sort[0];
  if (first?.key !== "name") return "NEWEST";
  return first.dir === "desc" ? "NAME_DESC" : "NAME_ASC";
}

/** `astroliftBrainsPage`'s variables for the list state. */
export function brainsVariables(
  filters: Record<string, string>,
  state: { q: string; sort: readonly SortState[]; pageSize: number; after: string | null }
): ListBrainsPageQueryVariables {
  return {
    search: state.q.trim() || null,
    team: filters.team || null,
    sort: brainSort(state.sort),
    includeArchived: filters.archived === "1",
    limit: state.pageSize,
    after: state.after,
  };
}

/** The declaration with the team filter offering `teams`. */
export function withTeamOptions(def: ListDefinition, teams: readonly string[]): ListDefinition {
  return {
    ...def,
    fields: def.fields.map((field) =>
      field.key === "team"
        ? { ...field, options: teams.map((t) => ({ value: t, label: t })) }
        : field
    ),
  };
}
