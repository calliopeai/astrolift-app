/**
 * The two lists a semantic layer's tabs hold (#2427): the teams it is
 * shared with and the apps hosted on it. Both come whole with the layer
 * (`astroliftBrain.sharedWith`, `.hostedApps`), so search, order and pages
 * are made from those rows (`selectRows`). Pure.
 */
import type { CopiedListDefinition } from "@/components/list/list-state";
import type { SelectRowsSpec } from "@/components/list/select-rows";

export interface ShareRow {
  team: string;
  access: string;
}

export const BRAIN_SHARES_LIST: CopiedListDefinition = {
  id: "brains.detail.shares",
  copy: "brainShares",
  fields: [],
  searchPlaceholder: "Search teams…",
  defaultSort: [{ key: "team", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50],
};

export const BRAIN_SHARES_SELECT: SelectRowsSpec<ShareRow> = {
  text: (row) => [row.team],
  sort: { team: (row) => row.team, access: (row) => row.access },
  id: (row) => row.team,
};

export interface HostedAppRow {
  slug: string;
  environments: string[];
}

export const BRAIN_APPS_LIST: CopiedListDefinition = {
  id: "brains.detail.apps",
  copy: "brainApps",
  fields: [],
  searchPlaceholder: "Search apps…",
  defaultSort: [{ key: "app", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50],
};

export const BRAIN_APPS_SELECT: SelectRowsSpec<HostedAppRow> = {
  text: (row) => [row.slug, ...row.environments],
  sort: { app: (row) => row.slug },
  id: (row) => row.slug,
};
