/**
 * Admin › App access (#2534): who may enter every app, per environment, and
 * whether the edge holds each rule. Pure: the list declaration, the list
 * state as the query's variables, and the mappers from the server's rows.
 *
 * `astroliftEdgeAccess` answers the list; with a `person` filter the list
 * asks `astroliftEdgeAccessForPerson` instead, which keeps the same rows and
 * adds how that person gets in. Mine is the viewer's own email (`me`).
 */
import type {
  AstroliftEdgeAccessStatus,
  EdgeAccessForPersonQuery,
  EdgeAccessQuery,
} from "@/graphql/__generated__/operations";

import { type CopiedListDefinition, standardViews } from "@/components/list/list-state";

export type EdgeStatus =
  | "enforced"
  | "open"
  | "applying"
  | "unverified"
  | "pending_deploy"
  | "drift"
  | "not_on_edge";

export const EDGE_STATUSES: EdgeStatus[] = [
  "enforced",
  "applying",
  "pending_deploy",
  "drift",
  "unverified",
  "open",
  "not_on_edge",
];

/** The statuses whose rule the edge does not hold yet, or may not: the Not enforced view. */
export const NOT_ENFORCED: EdgeStatus[] = ["applying", "pending_deploy", "drift", "unverified"];

/** How a person gets in, or why it cannot be told (`unknown`: groups unreadable). */
export type EntryVia = "user" | "group" | "open" | "unknown";

export interface EdgeAccessRow {
  /** `<app guid>:<environment guid>`. */
  id: string;
  appSlug: string;
  appName: string;
  environment: string;
  clusterSlug: string;
  hosts: string[];
  restricted: boolean;
  groups: string[];
  users: string[];
  /** `ui`, `manifest`, or empty when open. */
  source: string;
  setBy: string | null;
  setAt: string | null;
  status: EdgeStatus;
  /** When the edge last read the rules; null when no apply is on record. */
  edgeAppliedAt: string | null;
  /** The app's own access editor. */
  href: string;
  /** Set when the list is for one person. */
  entry?: { enters: boolean | null; via: EntryVia; groups: string[] };
}

/** The person in one cluster's identity provider. */
export interface PersonLogin {
  clusterSlug: string;
  resolved: boolean;
  reason: string;
  hasLogin: boolean | null;
  enabled: boolean | null;
  groups: string[];
}

export interface PersonSummary {
  email: string;
  logins: PersonLogin[];
  /** Environments, under the same filters, that refuse the person. */
  refusedCount: number;
}

export const EDGE_ACCESS_LIST: CopiedListDefinition = {
  id: "admin.appAccess",
  copy: "appAccess",
  fields: [
    {
      key: "status",
      label: "Edge",
      options: [...EDGE_STATUSES, "attention"].map((s) => ({ value: s, label: s })),
    },
    // An email, or `me`: the list becomes the apps that person may enter.
    { key: "person", label: "Person" },
    { key: "team", label: "Team" },
    { key: "project", label: "Project" },
    { key: "cluster", label: "Cluster" },
  ],
  // The server matches app, environment, cluster, hosts, groups and users.
  searchPlaceholder: "Search apps, hosts, groups, emails…",
  defaultSort: [{ key: "app", dir: "asc" }],
  views: standardViews(
    { person: "me" },
    [{ key: "attention", label: "Not enforced", filters: { status: "attention" } }],
    { mineNote: "The apps you may enter, with your own login." }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export interface EdgeAccessVariables {
  filter: {
    team?: string;
    project?: string;
    cluster?: string;
    status?: AstroliftEdgeAccessStatus[];
  } | null;
  search: string | null;
  first: number;
  after: string | null;
}

/** The list state as `astroliftEdgeAccess` variables; `person` picks the query, not a filter. */
export function edgeAccessVariables({
  filters,
  q,
  pageSize,
  after,
}: {
  filters: Record<string, string>;
  q: string;
  pageSize: number;
  after: string | null;
}): EdgeAccessVariables {
  const filter: NonNullable<EdgeAccessVariables["filter"]> = {};
  for (const key of ["team", "project", "cluster"] as const) {
    if (filters[key]) filter[key] = filters[key];
  }
  const status = filters.status;
  if (status) {
    const wanted = status === "attention" ? NOT_ENFORCED : [status];
    filter.status = wanted
      .filter((s) => (EDGE_STATUSES as string[]).includes(s))
      .map((s) => s.toUpperCase() as AstroliftEdgeAccessStatus);
  }
  return {
    filter: Object.keys(filter).length ? filter : null,
    search: q.trim() || null,
    first: pageSize,
    after,
  };
}

type ServerRow = EdgeAccessQuery["astroliftEdgeAccess"]["items"][number];
type ServerPersonPage = EdgeAccessForPersonQuery["astroliftEdgeAccessForPerson"];

const VIA = new Set<string>(["user", "group", "open", "unknown"]);

/** The app's Access tab, on its edge section: where a rule is changed. */
export function accessEditorHref(appSlug: string): string {
  return `/apps/${encodeURIComponent(appSlug)}/access?section=edge`;
}

export function fromEdgeAccessRow(r: ServerRow): EdgeAccessRow {
  return {
    id: r.id,
    appSlug: r.appSlug,
    appName: r.appName,
    environment: r.environment,
    clusterSlug: r.clusterSlug,
    hosts: r.hosts,
    restricted: r.restricted,
    groups: r.groups,
    users: r.users,
    source: r.source,
    setBy: r.setBy ?? null,
    setAt: r.setAt ?? null,
    status: r.status.toLowerCase() as EdgeStatus,
    edgeAppliedAt: r.edgeAppliedAt ?? null,
    href: accessEditorHref(r.appSlug),
  };
}

export function fromPersonPage(page: ServerPersonPage): {
  rows: EdgeAccessRow[];
  person: PersonSummary;
} {
  return {
    rows: page.items.map((item) => ({
      ...fromEdgeAccessRow(item.rule),
      entry: {
        enters: item.enters ?? null,
        via: (VIA.has(item.via) ? item.via : "unknown") as EntryVia,
        groups: item.viaGroups,
      },
    })),
    person: {
      email: page.email,
      logins: page.logins.map((l) => ({
        clusterSlug: l.clusterSlug,
        resolved: l.resolved,
        reason: l.reason,
        hasLogin: l.hasLogin ?? null,
        enabled: l.enabled ?? null,
        groups: l.groups,
      })),
      refusedCount: page.refusedCount,
    },
  };
}
