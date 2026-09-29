/**
 * The app Deployments tab as a list (spec 44 §4.4, §5.1): the declaration
 * and the pure steps the hook runs between the page query and the screen.
 *
 * Views: All · Mine · Waiting approval · Failed · Today · Previews. Previews
 * is the app's preview environments (AppPreviewsScreen), a different row
 * shape, so it has its own declaration that shares these views: the view
 * picker reads the same on both, and `?view=previews` picks the screen.
 *
 * What the server answers and what this file answers: `astroliftDeploymentsPage`
 * takes `appSlug`, `environmentName`, `statuses`, `search` and a cursor, so
 * the status and environment chips, Waiting approval, Failed and search are
 * server side. It has no initiator or time argument and no sort, so Mine and
 * Today are applied to each page here (`selectPage`), and the list keeps the
 * server's order (newest first) with no sortable columns.
 */
import type { SortState } from "@/components/data-table";
import {
  type ListDefinition,
  type ListFieldOption,
  type ListView,
  standardViews,
} from "@/components/list/use-list-state";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";

export const PREVIEWS_VIEW = "previews";

/** Shared by the deployments list and the previews list, so the picker is one menu. */
export const APP_DEPLOYMENT_VIEWS: ListView[] = standardViews(
  { startedBy: "me" },
  [
    { key: "waiting", label: "Waiting approval", filters: { status: "pending_approval" } },
    { key: "failed", label: "Failed", filters: { status: "failed" } },
    { key: "today", label: "Today", filters: { since: "today" } },
    { key: PREVIEWS_VIEW, label: "Previews", filters: {} },
  ],
  {
    mineNote:
      "Mine shows the deployments you triggered on each page the server returns; the server cannot filter by who triggered yet.",
  }
);

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

/** The page query's variables for the effective filters (view plus chips). */
export function pageVariables(
  appSlug: string,
  filters: Record<string, string>,
  q: string,
  pageSize: number,
  after: string | null
) {
  return {
    appSlug,
    environmentName: filters.env || null,
    statuses: filters.status ? [filters.status] : null,
    search: q.trim() || null,
    limit: pageSize,
    after,
  };
}

function startOfDay(now: number): number {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function when(d: Pick<AstroliftDeployment, "startedAt" | "createdAt">): number {
  return Date.parse(d.startedAt ?? d.createdAt);
}

/**
 * What the server cannot answer yet, applied to one page.
 *
 * Today: the page is newest first, so today's deployments are a prefix of
 * the whole walk. Once a row older than today shows up there is nothing
 * further to fetch, and the next cursor is dropped. Exact, not a sample.
 *
 * Mine (`startedBy: me`): the viewer's rows on this page. Not a prefix, so
 * the cursor stays and older pages can still hold some; the view's note
 * says so.
 */
export function selectPage<
  T extends Pick<AstroliftDeployment, "startedAt" | "createdAt" | "triggeredByMe">,
>(
  rows: T[],
  nextCursor: string | null,
  filters: Record<string, string>,
  now: number
): { rows: T[]; nextCursor: string | null } {
  let out = rows;
  let cursor = nextCursor;
  if (filters.since === "today") {
    const from = startOfDay(now);
    out = out.filter((d) => when(d) >= from);
    if (out.length < rows.length) cursor = null;
  }
  if (filters.startedBy === "me") out = out.filter((d) => d.triggeredByMe);
  return { rows: out, nextCursor: cursor };
}

/** The deployment's run page (spec 44 §5.5), moving onto RunPage. */
export function deploymentHref(id: string): string {
  return `/deployments/${encodeURIComponent(id)}`;
}
