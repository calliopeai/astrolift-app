/**
 * The Apps list declaration (spec 44 §5.1, §4.4) and the variables it sends.
 *
 * `astroliftAppsPage` (Mine: `astroliftMyAppsPage`) takes the §5.1 contract
 * (#2149): the Failing and Archived views, the status, last deploy, kind,
 * cluster and project chips, search, column sort and numbered pages all run
 * on the server, and each row carries its topology and clusters. The one
 * step left here is the per-browser pin set, which reorders the page in
 * hand (`pinFirst`).
 */
import type { SortState } from "@/components/data-table";
import { formatSort, type ListDefinition, standardViews } from "@/components/list/list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import type {
  AppHealthPulseStatus,
  AstroliftRegisteredApp,
} from "@/graphql/registry/registry.types";
import { NAV } from "@/lib/shell/nav-model";
import { TOPOLOGY_META, type TopologyKind } from "@/lib/topology";

/** One row: the app, with the topology and clusters the page returns, typed. */
export type AppRow = AstroliftRegisteredApp & {
  /** `topologyKind`; null until the app has workloads to classify. */
  topology: TopologyKind | null;
  /** `clusterSlugs`: the clusters its environments deploy to, in environment order. */
  clusters: string[];
};

/** The header status, as the app's own header shows it (AppFrame). */
export type AppStatusKey = "live" | "ready" | "pending" | "provisioning" | "failed" | "archived";

export const APP_STATUS_ORDER: AppStatusKey[] = [
  "live",
  "ready",
  "pending",
  "provisioning",
  "failed",
  "archived",
];

export function appStatusKey(app: AstroliftRegisteredApp): AppStatusKey {
  if (app.isArchived) return "archived";
  if (app.provisioningStatus === "ready" && app.managedHostname) return "live";
  return app.provisioningStatus;
}

export const DEPLOY_LABEL: Record<AppHealthPulseStatus, string> = {
  OK: "Healthy",
  DEGRADED: "Failed",
  STALE: "Stale",
  NEVER: "Never deployed",
};

export const APPS_LIST: ListDefinition = {
  id: "apps",
  fields: [
    // Free text: matched on the project slug or name.
    { key: "project", label: "Project" },
    {
      key: "status",
      label: "Status",
      // Archived is its own view, not a status chip.
      options: APP_STATUS_ORDER.filter((s) => s !== "archived").map((value) => ({
        value,
        label: value,
      })),
    },
    {
      key: "deploy",
      label: "Last deploy",
      options: (Object.keys(DEPLOY_LABEL) as AppHealthPulseStatus[]).map((value) => ({
        value: value.toLowerCase(),
        label: DEPLOY_LABEL[value],
      })),
    },
    {
      key: "kind",
      label: "Kind",
      options: (Object.keys(TOPOLOGY_META) as TopologyKind[]).map((value) => ({
        value,
        label: TOPOLOGY_META[value].label,
      })),
    },
    // Free text: matched on a cluster slug any of the app's environments use.
    { key: "cluster", label: "Cluster" },
  ],
  // The server matches name, slug, description and repo.
  searchPlaceholder: "Search apps, slugs, repos...",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews(
    {},
    [
      { key: "failing", label: "Failing", filters: { failing: "1" } },
      { key: "archived", label: "Archived", filters: { archived: "1" } },
    ],
    {
      mineNote:
        "Mine means apps you hold a role on, directly or through their project, team or organization, until apps record who owns them.",
    }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** Translate presentation while preserving URL keys, API values and view filters. */
export function localizedAppsList(
  t: (key: string) => string,
  status: (key: string) => string
): ListDefinition {
  return {
    ...APPS_LIST,
    searchPlaceholder: t("search.placeholder"),
    fields: APPS_LIST.fields.map((field) => ({
      ...field,
      label: t(`columns.${field.key === "deploy" ? "lastDeploy" : field.key}`),
      options: field.options?.map((option) => ({
        ...option,
        label:
          field.key === "status"
            ? status(`status.${option.value}`)
            : field.key === "deploy"
              ? t(`freshness.status.${option.value.toUpperCase()}`)
              : t(`kinds.${option.value}`),
      })),
    })),
    views: APPS_LIST.views.map((view) => ({
      ...view,
      label: t(`views.${view.key}`),
      ...(view.key === "mine" ? { note: t("views.mineNote") } : {}),
    })),
  };
}

/** `Apps ▾` [› tail]: the first crumb switches between the Apps area's functions. */
export function appsCrumbs(tail?: Crumb): Crumb[] {
  const area = NAV.find((a) => a.key === "apps");
  const crumbs: Crumb[] = [
    {
      label: area?.label ?? "Apps",
      switcher: (area?.groups ?? []).flatMap((g) =>
        g.functions.map((f) => ({ label: f.label, href: f.href, active: f.key === "apps" }))
      ),
    },
  ];
  if (tail) crumbs.push(tail);
  return crumbs;
}

/** The filter input's shape on `astroliftAppsPage` (AstroliftAppsListFilter). */
export interface AppsListFilter {
  archived?: boolean;
  failing?: boolean;
  status?: string[];
  deploy?: string[];
  kind?: string[];
  cluster?: string[];
  project?: string[];
}

export interface AppsPageVariables {
  includeFreshness: true;
  search: string | null;
  filter: AppsListFilter | null;
  sort: string;
  page: number;
  pageSize: number;
}

/**
 * What the page query is sent for a list state: the view's filters under
 * the person's chips, search, sort and the numbered page. The status and
 * last-deploy chips hold the lower-case values the URL carries; the schema
 * takes them as upper-case enum members.
 */
export function appsPageVariables({
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
}): AppsPageVariables {
  const filter: AppsListFilter = {};
  if (filters.archived) filter.archived = true;
  if (filters.failing) filter.failing = true;
  if (filters.status) filter.status = [filters.status.toUpperCase()];
  if (filters.deploy) filter.deploy = [filters.deploy.toUpperCase()];
  for (const key of ["kind", "cluster", "project"] as const)
    if (filters[key]) filter[key] = [filters[key]];
  return {
    includeFreshness: true,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    sort: formatSort(sort),
    page: Math.max(1, page),
    pageSize,
  };
}

/**
 * The viewer's pinned apps lifted to the top of the page in hand (#697):
 * pins are a per-browser preference, so a pin on page three stays on page
 * three.
 */
export function pinFirst<T extends { slug: string }>(rows: T[], pinned: ReadonlySet<string>): T[] {
  if (pinned.size === 0) return rows;
  return [...rows.filter((a) => pinned.has(a.slug)), ...rows.filter((a) => !pinned.has(a.slug))];
}
