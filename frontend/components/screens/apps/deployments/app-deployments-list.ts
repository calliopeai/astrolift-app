/**
 * The app Deployments tab as a list (spec 44 §4.4, §5.1): the declaration
 * and the pure steps the hook runs between the page query and the screen.
 *
 * Views: All · Mine · Waiting approval · Failed · Today · Previews. Previews
 * is the app's preview environments (AppPreviewsScreen), a different row
 * shape, so it has its own declaration that shares these views: the view
 * picker reads the same on both, and `?view=previews` picks the screen.
 *
 * Every view and chip is answered by `astroliftDeploymentsPage`: status,
 * environment and search as its own arguments, Mine (who triggered it) and
 * Today (started since local midnight) through its `filter`, and the list's
 * order (newest start first) as `sort` (#2155).
 */
import type { SortState } from "@/components/data-table";
import {
  formatSort,
  type ListDefinition,
  type ListFieldOption,
  type ListView,
  standardViews,
} from "@/components/list/list-state";
import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";

export const PREVIEWS_VIEW = "previews";

/** Shared by the deployments list and the previews list, so the picker is one menu. */
export const APP_DEPLOYMENT_VIEWS: ListView[] = standardViews({ startedBy: "me" }, [
  { key: "waiting", label: "Waiting approval", filters: { status: "pending_approval" } },
  { key: "failed", label: "Failed", filters: { status: "failed" } },
  { key: "today", label: "Today", filters: { since: "today" } },
  { key: PREVIEWS_VIEW, label: "Previews", filters: {} },
]);

export const DEPLOYMENT_STATUS_LABEL: Record<DeploymentStatus, string> = {
  pending_approval: "Pending approval",
  pending: "Pending",
  deploying: "Deploying",
  redeploying: "Redeploying",
  running: "Running",
  failed: "Failed",
  superseded: "Superseded",
  rolled_back: "Rolled back",
};

const STATUS_OPTIONS: ListFieldOption[] = (
  Object.keys(DEPLOYMENT_STATUS_LABEL) as DeploymentStatus[]
).map((s) => ({ value: s, label: DEPLOYMENT_STATUS_LABEL[s] }));

const NEWEST_FIRST: SortState[] = [{ key: "started", dir: "desc" }];

/**
 * The deployments list. `environments` are the app's own, as the env chip's
 * options; call it with a stable array (the hook memoises on the names).
 * `previews: false` drops the Previews view where no route serves it (an
 * agent's deployments, under the agent shell).
 */
export function appDeploymentsList(
  environments: string[],
  { previews = true }: { previews?: boolean } = {}
): ListDefinition {
  return {
    id: "apps.deployments",
    fields: [
      { key: "status", label: "Status", options: STATUS_OPTIONS },
      {
        key: "env",
        label: "Environment",
        options: environments.map((e) => ({ value: e, label: e })),
      },
    ],
    searchPlaceholder: "Search tags, commits, branches…",
    defaultSort: NEWEST_FIRST,
    views: previews
      ? APP_DEPLOYMENT_VIEWS
      : APP_DEPLOYMENT_VIEWS.filter((v) => v.key !== PREVIEWS_VIEW),
    paging: "cursor",
    pageSizes: [25, 50, 100],
  };
}

/** The previews view: the app's preview environments, searched on the server. */
export const APP_PREVIEWS_LIST: ListDefinition = {
  id: "apps.previews",
  fields: [],
  searchPlaceholder: "Search branch, host, commit or status…",
  defaultSort: NEWEST_FIRST,
  views: APP_DEPLOYMENT_VIEWS,
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

function startOfDay(now: number): string {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

/**
 * The page query's variables for the effective filters (view plus chips).
 * `now` places Today's midnight; the hook fixes it per visit. The route
 * preloads the All view's first page with these exact values.
 */
export function pageVariables(
  appSlug: string,
  filters: Record<string, string>,
  {
    q,
    sort,
    pageSize,
    after,
  }: { q: string; sort: SortState[]; pageSize: number; after: string | null },
  now: number
) {
  const filter: { triggeredBy?: string[]; startedAfter?: string } = {};
  if (filters.startedBy) filter.triggeredBy = [filters.startedBy];
  if (filters.since === "today") filter.startedAfter = startOfDay(now);
  return {
    appSlug,
    environmentName: filters.env || null,
    statuses: filters.status ? [filters.status] : null,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    sort: formatSort(sort),
    limit: pageSize,
    after,
  };
}

/** The deployment's run page (spec 44 §5.5), moving onto RunPage. */
export function deploymentHref(id: string): string {
  return `/deployments/${encodeURIComponent(id)}`;
}
