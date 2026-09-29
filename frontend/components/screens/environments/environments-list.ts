/**
 * Apps › Environments (spec 44 §4.4, §5.1): the list declaration and the
 * variables it sends. `astroliftEnvironmentsPage` answers every view, chip,
 * search, sort and numbered page (#2155): Production and Previews filter on
 * the environment's recorded kind, Mine on its owner (the environment's
 * creator, else its app's).
 */
import type { SortState } from "@/components/data-table";
import { formatSort, type ListDefinition, standardViews } from "@/components/list/list-state";

export const ENVIRONMENTS_LIST: ListDefinition = {
  id: "apps.environments",
  fields: [
    { key: "app", label: "App" },
    { key: "cluster", label: "Cluster" },
  ],
  // The server matches the environment name, app slug and name, cluster slug and region.
  searchPlaceholder: "Search environments, apps, clusters, regions…",
  defaultSort: [
    { key: "app", dir: "asc" },
    { key: "name", dir: "asc" },
  ],
  views: standardViews({ owner: "me" }, [
    { key: "production", label: "Production", filters: { kind: "production" } },
    { key: "previews", label: "Previews", filters: { kind: "preview" } },
  ]),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** The filter input's shape on `astroliftEnvironmentsPage` (AstroliftEnvironmentsFilter). */
export interface EnvironmentsFilter {
  kind?: string[];
  app?: string[];
  cluster?: string[];
  owner?: string[];
}

/** What `astroliftEnvironmentsPage` is sent for a list state, fleet-wide or one app's. */
export function environmentsVariables(
  appSlug: string | null,
  {
    q,
    filters,
    sort,
    page,
    pageSize,
  }: {
    q: string;
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  const filter: EnvironmentsFilter = {};
  for (const key of ["kind", "app", "cluster", "owner"] as const)
    if (filters[key]) filter[key] = [filters[key]];
  return {
    appSlug,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    sort: formatSort(sort),
    page: Math.max(1, page),
    pageSize,
  };
}
