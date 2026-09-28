/**
 * Apps › Environments (spec 44 §4.4, §5.1): the list declaration and the
 * filter, sort and page step the hook runs. `astroliftEnvironments` returns
 * every environment the viewer may see in one list with no arguments but the
 * app, so views, chips, search, sort and numbered pages all run here.
 *
 * Production and Previews go by the environment's name (`prod`,
 * `production`; `preview-…`, `pr-…`) until environments record their kind;
 * the views say so. Environments record no owner, so Mine is empty.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

export type EnvironmentKind = "production" | "preview" | "other";

/** What an environment is, read from its name. */
export function environmentKind(name: string): EnvironmentKind {
  const n = name.toLowerCase();
  if (n === "prod" || n === "production" || n.startsWith("prod-") || n.startsWith("production-"))
    return "production";
  if (n.startsWith("preview") || n.startsWith("pr-")) return "preview";
  return "other";
}

const BY_NAME =
  "Production and Previews go by the environment's name until environments record their kind.";

export const ENVIRONMENTS_LIST: ListDefinition = {
  id: "apps.environments",
  fields: [
    { key: "app", label: "App" },
    { key: "cluster", label: "Cluster" },
    {
      key: "deploys",
      label: "Deploys",
      options: [
        { value: "active", label: "active" },
        { value: "paused", label: "paused" },
      ],
    },
  ],
  searchPlaceholder: "Search environments, apps, clusters, URLs…",
  defaultSort: [
    { key: "app", dir: "asc" },
    { key: "name", dir: "asc" },
  ],
  views: standardViews(
    { owner: "me" },
    [
      { key: "production", label: "Production", filters: { kind: "production" }, note: BY_NAME },
      { key: "previews", label: "Previews", filters: { kind: "preview" }, note: BY_NAME },
    ],
    { mineNote: "Environments do not record an owner yet, so Mine is empty." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const SORT_VALUE: Record<string, (e: AstroliftAppEnvironment) => string | number> = {
  app: (e) => e.registeredAppSlug.toLowerCase(),
  name: (e) => e.name.toLowerCase(),
  cluster: (e) => e.clusterSlug ?? "",
  approvals: (e) => e.requiredApprovals,
};

function matches(e: AstroliftAppEnvironment, filters: Record<string, string>, q: string) {
  if (filters.owner) return false;
  if (filters.kind && environmentKind(e.name) !== filters.kind) return false;
  if (filters.app && e.registeredAppSlug !== filters.app) return false;
  if (filters.cluster && e.clusterSlug !== filters.cluster) return false;
  if (filters.deploys && (e.deploysPaused ? "paused" : "active") !== filters.deploys) return false;
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [e.name, e.registeredAppSlug, e.clusterSlug ?? "", e.url, e.id].some((f) =>
    f.toLowerCase().includes(needle)
  );
}

/** One numbered page of environments; `totalCount` is the filtered count. */
export function selectEnvironments(
  all: AstroliftAppEnvironment[],
  {
    filters,
    q,
    sort,
    page,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): { rows: AstroliftAppEnvironment[]; totalCount: number } {
  const kept = all
    .filter((e) => matches(e, filters, q))
    .sort((a, b) => {
      for (const s of sort) {
        const value = SORT_VALUE[s.key];
        if (!value) continue;
        const x = value(a);
        const y = value(b);
        if (x < y) return s.dir === "asc" ? -1 : 1;
        if (x > y) return s.dir === "asc" ? 1 : -1;
      }
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
}
