/**
 * The Apps list declaration (spec 44 §5.1, §4.4) and the pure step the hook
 * runs over the registry: views, filters, sort and numbered pages.
 *
 * Why most of this runs here and not on the server: `astroliftAppsPage`
 * takes `search`, `teamSlug`, `projectSlug`, a health `status`, `sourceKind`,
 * three fixed sorts and a cursor. It has no page number, no archived-only
 * filter, and apps carry no cluster or topology, so the Failing and Archived
 * views, the status, kind and cluster filters, column sort and numbered
 * pages have no server argument yet. The hook walks every app for the current
 * search (Mine through `astroliftMyAppsPage`), joins in the org's environments
 * and workloads, and `selectApps` answers the rest, as the Clusters list does.
 * When the backend grows the §5.1 contract the hook sends `list.filters`,
 * `sort` and `page` instead and this step goes away; the screen does not
 * change.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import type {
  AppHealthPulseStatus,
  AstroliftRegisteredApp,
} from "@/graphql/registry/registry.types";
import { NAV } from "@/lib/shell/nav-model";
import { TOPOLOGY_META, type TopologyKind } from "@/lib/topology";

/** One row: the app, plus what the list learns from its workloads and environments. */
export type AppRow = AstroliftRegisteredApp & {
  /** Null until the app has workloads to classify. */
  topology: TopologyKind | null;
  /** Clusters its environments deploy to, in environment order. */
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

/** Failing: provisioning failed, or the latest deploy did. */
export function isFailing(app: AstroliftRegisteredApp): boolean {
  return app.provisioningStatus === "failed" || app.healthPulse?.status === "DEGRADED";
}

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

type SortValue = string | number;

const time = (ts: string | null | undefined) => (ts ? Date.parse(ts) : 0);

const SORT_VALUE: Record<string, (a: AppRow) => SortValue> = {
  name: (a) => a.name.toLowerCase(),
  status: (a) => APP_STATUS_ORDER.indexOf(appStatusKey(a)),
  // Never deployed sorts before the oldest deploy.
  deployed: (a) => time(a.lastDeployedAt ?? a.latestDeployment?.createdAt),
  created: (a) => time(a.createdAt),
};

const lower = (s: string | null | undefined) => (s ?? "").toLowerCase();

function matches(a: AppRow, filters: Record<string, string>): boolean {
  // Only the Archived view holds archived apps (the server hides them elsewhere).
  if (Boolean(filters.archived) !== Boolean(a.isArchived)) return false;
  if (filters.failing && !isFailing(a)) return false;
  if (filters.status && appStatusKey(a) !== lower(filters.status)) return false;
  if (filters.deploy && lower(a.healthPulse?.status) !== lower(filters.deploy)) return false;
  if (filters.kind && a.topology !== filters.kind) return false;
  if (filters.project) {
    const p = lower(filters.project);
    if (lower(a.projectSlug) !== p && lower(a.projectName) !== p) return false;
  }
  if (filters.cluster && !a.clusters.some((c) => lower(c) === lower(filters.cluster))) return false;
  return true;
}

function compare(a: AppRow, b: AppRow, sort: SortState[]): number {
  for (const s of sort) {
    const value = SORT_VALUE[s.key];
    if (!value) continue;
    const x = value(a);
    const y = value(b);
    if (x < y) return s.dir === "asc" ? -1 : 1;
    if (x > y) return s.dir === "asc" ? 1 : -1;
  }
  return a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0;
}

/**
 * One numbered page of the registry: view filters and chips applied, sorted,
 * sliced, and the viewer's pinned apps lifted to the top of the page in hand
 * (#697: pins are a per-browser preference, so a pin on page three stays on
 * page three). `totalCount` is the filtered count, for "1–25 of 140".
 */
export function selectApps(
  apps: AppRow[],
  {
    filters,
    sort,
    page,
    pageSize,
    pinned = new Set<string>(),
  }: {
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
    pinned?: ReadonlySet<string>;
  }
): { rows: AppRow[]; totalCount: number } {
  const kept = apps.filter((a) => matches(a, filters)).sort((a, b) => compare(a, b, sort));
  const start = (Math.max(1, page) - 1) * pageSize;
  const slice = kept.slice(start, start + pageSize);
  const rows = [
    ...slice.filter((a) => pinned.has(a.slug)),
    ...slice.filter((a) => !pinned.has(a.slug)),
  ];
  return { rows, totalCount: kept.length };
}
