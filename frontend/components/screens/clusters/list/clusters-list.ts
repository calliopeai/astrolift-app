/**
 * The Clusters list declaration (spec 44 §5.1) and the pure pieces the list
 * and detail screens share: the Admin breadcrumb, provider and lifecycle
 * labels, and the filter/sort/page step the hook runs over the fleet.
 *
 * Why filtering runs here and not on the server: `astroliftClustersPage`
 * takes `search`, `limit` and `after` only (a `(slug, guid)` keyset walk), so
 * provider, status, the Offline and Mine views, column sort and numbered
 * pages have no server argument yet. The hook walks the whole fleet for the
 * current search and `selectClusters` answers the rest. When the backend
 * grows the §5.1 contract, the hook sends `list.filters` / `sort` / `page`
 * instead and this step goes away; the screen does not change.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import { NAV } from "@/lib/shell/nav-model";

import type { ClusterRow } from "./use-clusters-list";

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

/** The value Mine filters on: clusters whose last setup run the viewer started. */
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
  views: standardViews(
    { setupBy: MINE },
    [{ key: "offline", label: "Offline", filters: { live: "offline" } }],
    {
      mineNote:
        "Mine means clusters whose last setup you ran, until clusters record who registered them.",
    }
  ),
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

type SortValue = string | number;

const SORT_VALUE: Record<string, (c: ClusterRow) => SortValue> = {
  name: (c) => c.name.toLowerCase(),
  slug: (c) => c.slug,
  status: (c) => c.lifecycle,
  provider: (c) => c.providerPluginSlug,
  region: (c) => c.region,
  live: (c) => c.heartbeatStatus ?? "never_seen",
  // Never probed sorts before the oldest probe.
  lastProbe: (c) => (c.capabilitiesProbedAt ? Date.parse(c.capabilitiesProbedAt) : 0),
};

function matches(c: ClusterRow, filters: Record<string, string>, me: string | null): boolean {
  if (filters.provider && c.providerPluginSlug !== filters.provider) return false;
  if (filters.status && c.lifecycle !== filters.status) return false;
  if (filters.live && (c.heartbeatStatus ?? "never_seen") !== filters.live) return false;
  if (filters.setupBy === MINE) {
    const by = c.lastBootstrapRun?.triggeredByUsername;
    if (!me || !by || by !== me) return false;
  }
  return true;
}

function compare(a: ClusterRow, b: ClusterRow, sort: SortState[]): number {
  for (const s of sort) {
    const value = SORT_VALUE[s.key];
    if (!value) continue;
    const x = value(a);
    const y = value(b);
    if (x < y) return s.dir === "asc" ? -1 : 1;
    if (x > y) return s.dir === "asc" ? 1 : -1;
  }
  // The server's own order breaks ties, so a page never reshuffles.
  return a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0;
}

/**
 * One numbered page of the fleet: view filters and chips applied, sorted,
 * sliced. `totalCount` is the filtered count, for "1–25 of 140".
 */
export function selectClusters(
  fleet: ClusterRow[],
  {
    filters,
    sort,
    page,
    pageSize,
    me,
  }: {
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
    me: string | null;
  }
): { rows: ClusterRow[]; totalCount: number } {
  const kept = fleet.filter((c) => matches(c, filters, me)).sort((a, b) => compare(a, b, sort));
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
}
