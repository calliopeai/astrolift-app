/**
 * Apps › Semantic layers (#2320, #2427): the brain apps the viewer can read.
 * The server answers the search; `astroliftBrains` takes no team, sort or
 * paging argument and returns at most 200 rows, so the team filter, the
 * order and the pages are made from the rows in hand (`selectRows`). Pages
 * stay short because each visible row reads its own status. Pure.
 */
import type { CopiedListDefinition, ListDefinition } from "@/components/list/list-state";
import type { SelectRowsSpec } from "@/components/list/select-rows";
import type { ListBrainsQuery } from "@/graphql/__generated__/operations";

export type BrainRow = ListBrainsQuery["astroliftBrains"][number];

export const BRAINS_LIST: CopiedListDefinition = {
  id: "apps.brains",
  copy: "brains",
  // Options are the teams of the rows in hand (`withTeamOptions`).
  fields: [{ key: "team", label: "Team" }],
  searchPlaceholder: "Search names and slugs…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [10, 25],
};

export const BRAINS_SELECT: SelectRowsSpec<BrainRow> = {
  filter: { team: (row, value) => row.team === value },
  text: (row) => [row.name, row.slug],
  sort: {
    name: (row) => row.name.toLocaleLowerCase(),
    team: (row) => `${row.team}/${row.project}`.toLocaleLowerCase(),
  },
  id: (row) => row.id,
};

/** The teams the rows belong to, once each, in order. */
export function brainTeams(rows: readonly BrainRow[]): string[] {
  return [...new Set(rows.map((r) => r.team).filter(Boolean))].sort();
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
