/**
 * The Clusters list declaration (spec 44 §5.1) and the pure pieces the list
 * and detail screens share: the Admin breadcrumb, provider and lifecycle
 * labels, and the list state spelled as `astroliftClustersPage` variables.
 *
 * The server answers every filter, sort and page (#2150): provider, status,
 * the Offline view (`live`), Mine (`registeredBy: "me"`), the column sorts
 * and numbered pages with an exact `totalCount`. Nothing is filtered or
 * sorted in the browser.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, formatSort, standardViews } from "@/components/list/list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import { NAV } from "@/lib/shell/nav-model";

export type Lifecycle = "registered" | "managing" | "managed" | "error";

export const PROVIDER_LABEL: Record<string, string> = {
  k8s_native: "Kubernetes",
  eks: "AWS EKS",
  gke: "Google GKE",
  aks: "Azure AKS",
  k3s: "k3s",
  kind: "kind",
};

export const LIFECYCLE_LABEL: Record<Lifecycle, string> = {
  registered: "Registered",
  managing: "Managing…",
  managed: "Managed",
  error: "Error",
};

export function providerLabel(slug: string): string {
  return PROVIDER_LABEL[slug] ?? slug;
}

/** The value Mine filters on: clusters the viewer registered. */
export const MINE = "me";

export const CLUSTERS_LIST: ListDefinition = {
  id: "admin.clusters",
  fields: [
    {
      key: "provider",
      label: "Provider",
      options: Object.entries(PROVIDER_LABEL).map(([value, label]) => ({ value, label })),
    },
    {
      key: "status",
      label: "Status",
      options: (Object.keys(LIFECYCLE_LABEL) as Lifecycle[]).map((value) => ({
        value,
        label: value,
      })),
    },
  ],
  // The server matches name, slug, endpoint, region and provider slug.
  searchPlaceholder: "Search clusters...",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ registeredBy: MINE }, [
    { key: "offline", label: "Offline", filters: { live: "offline" } },
  ]),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** Admin ▾ › Clusters [› name]: the first crumb switches between the Admin functions. */
export function clusterCrumbs(name?: string): Crumb[] {
  const admin = NAV.find((a) => a.key === "admin");
  const switcher = (admin?.groups ?? []).flatMap((g) =>
    g.functions.map((f) => ({ label: f.label, href: f.href, active: f.key === "clusters" }))
  );
  const crumbs: Crumb[] = [{ label: "Admin", switcher }];
  crumbs.push(
    name === undefined ? { label: "Clusters" } : { label: "Clusters", href: "/clusters" }
  );
  if (name !== undefined) crumbs.push({ label: name });
  return crumbs;
}

/** The `filter` fields `astroliftClustersPage` declares, by list filter key. */
const FILTER_KEYS = ["provider", "status", "live", "registeredBy"] as const;

export interface ClustersListFilter {
  provider?: string[];
  status?: string[];
  live?: string[];
  registeredBy?: string[];
}

export interface ClustersPageVariables {
  search: string | null;
  filter: ClustersListFilter | null;
  sort: string;
  page: number;
  pageSize: number;
}

/**
 * The list state as `astroliftClustersPage` variables. `sort` is always
 * sent, which selects numbered paging on the server. An empty filter is
 * `null`, so a cold load asks exactly what `app/(app)/clusters/page.tsx`
 * preloads.
 */
export function clustersPageVariables({
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
}): ClustersPageVariables {
  const filter: ClustersListFilter = {};
  for (const key of FILTER_KEYS) if (filters[key]) filter[key] = [filters[key]];
  return {
    search: q.trim() || null,
    filter: Object.keys(filter).length ? filter : null,
    sort: formatSort(sort),
    page: Math.max(1, page),
    pageSize,
  };
}
