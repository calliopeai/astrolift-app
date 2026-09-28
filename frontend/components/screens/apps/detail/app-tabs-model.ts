import type { DetailTab } from "@/components/DetailPageTabs";

/**
 * The app detail's one row of tabs, named by function (spec 44 §5.2, §10.1,
 * §10.3): Overview · Deployments · Workloads · Logs & metrics · Domains ·
 * Secrets · Access · Settings. Each tab is its own route under
 * `<base>/[slug]`; the routes each tab absorbed (`owns`) redirect into it
 * and still light it up, so the row highlights for every nested path.
 */
export type AppTabKey =
  | "overview"
  | "deployments"
  | "workloads"
  | "logs"
  | "domains"
  | "secrets"
  | "access"
  | "settings";

export interface AppTabSpec {
  key: AppTabKey;
  /** i18n key under `apps.tabs.*`. */
  label: string;
  /** The route segment under `<base>/[slug]`; empty for Overview. */
  segment: string;
  /** Former routes this tab absorbed (each now redirects here). */
  owns: string[];
}

export const APP_TABS: readonly AppTabSpec[] = [
  { key: "overview", label: "overview", segment: "", owns: ["topology"] },
  { key: "deployments", label: "deployments", segment: "deployments", owns: ["previews"] },
  {
    key: "workloads",
    label: "workloads",
    segment: "workloads",
    owns: ["jobs", "managed-services"],
  },
  {
    key: "logs",
    label: "logsMetrics",
    segment: "logs",
    owns: ["observability", "shell", "console", "commands"],
  },
  { key: "domains", label: "domains", segment: "domains", owns: [] },
  { key: "secrets", label: "secrets", segment: "secrets", owns: [] },
  { key: "access", label: "access", segment: "access", owns: ["members", "tokens", "security"] },
  {
    key: "settings",
    label: "settings",
    segment: "settings",
    owns: ["config", "manifest", "webhooks", "environments"],
  },
];

/** `<base>/<slug>` or `<base>/<slug>/<segment>`. */
export function appTabHref(basePath: string, slug: string, tab: AppTabKey): string {
  const segment = APP_TABS.find((t) => t.key === tab)?.segment ?? "";
  return segment ? `${basePath}/${slug}/${segment}` : `${basePath}/${slug}`;
}

function tabFor(name: string): AppTabKey | undefined {
  return APP_TABS.find((t) => t.key === name || t.segment === name || t.owns.includes(name))?.key;
}

/**
 * The tab that owns a pathname: the first segment after `<base>/<slug>`
 * picks it, so `/apps/acme/workloads/web` is Workloads. An explicit `active`
 * (a tab key or an absorbed route's name) is the fallback for a path this
 * model does not know; Overview is the default.
 */
export function resolveAppTab(
  basePath: string,
  pathname: string,
  slug: string,
  active?: string
): AppTabKey {
  const root = `${basePath}/${slug}`;
  if (pathname === root || pathname === `${root}/`) return "overview";
  if (pathname.startsWith(`${root}/`)) {
    const segment = pathname.slice(root.length + 1).split(/[/?#]/)[0];
    const owner = tabFor(segment);
    if (owner) return owner;
  }
  return (active && tabFor(active)) || "overview";
}

/** The row as `DetailTab`s, labels resolved by the caller. */
export function appTabs(
  basePath: string,
  slug: string,
  pathname: string,
  label: (key: string) => string,
  active?: string
): DetailTab[] {
  const current = resolveAppTab(basePath, pathname, slug, active);
  return APP_TABS.map((tab) => ({
    key: tab.key,
    label: label(tab.label),
    href: appTabHref(basePath, slug, tab.key),
    active: tab.key === current,
  }));
}

/** One section inside a tab: `?<param>=<value>` picks it; the first is the default. */
export interface AppTabSection {
  id: string;
  /** i18n key under `apps.frame.sections.*`. */
  label: string;
  /** The query that selects it; empty for the tab's default section. */
  query: Record<string, string>;
}

const bySection = (...ids: [string, string][]): AppTabSection[] =>
  ids.map(
    ([id, label], i): AppTabSection => ({
      id,
      label,
      query: i === 0 ? {} : { section: id },
    })
  );

/**
 * The sections the consolidated tabs hold (spec 44 §5.2): what each former
 * route became. Deployments keeps previews as a view (`?view=previews`),
 * Workloads keeps scheduled jobs as a kind (`?kind=cronjob`); the rest are
 * `?section=`.
 */
export const APP_TAB_SECTIONS: Partial<Record<AppTabKey, AppTabSection[]>> = {
  deployments: [
    { id: "deployments", label: "deployments", query: {} },
    { id: "previews", label: "previews", query: { view: "previews" } },
  ],
  workloads: [
    { id: "workloads", label: "workloads", query: {} },
    { id: "jobs", label: "jobs", query: { kind: "cronjob" } },
    { id: "managed-services", label: "managedServices", query: { section: "managed-services" } },
  ],
  logs: bySection(
    ["logs", "logs"],
    ["metrics", "metrics"],
    ["console", "console"],
    ["commands", "commands"]
  ),
  access: bySection(
    ["members", "members"],
    ["tokens", "tokens"],
    ["security", "security"],
    ["edge", "edge"]
  ),
  settings: bySection(
    ["general", "general"],
    ["configuration", "configuration"],
    ["manifest", "manifest"],
    ["webhooks", "webhooks"],
    ["environments", "environments"],
    ["danger-zone", "dangerZone"]
  ),
};

export type SearchParams = Record<string, string | string[] | undefined>;

function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** The section a tab's query selects, or its default. */
export function activeSection(tab: AppTabKey, params: SearchParams): string {
  const sections = APP_TAB_SECTIONS[tab] ?? [];
  const hit = sections.find(
    (s) =>
      Object.keys(s.query).length > 0 &&
      Object.entries(s.query).every(([k, v]) => first(params[k]) === v)
  );
  return (hit ?? sections[0])?.id ?? "";
}

/** A section's href: the tab's route plus the section's query. */
export function sectionHref(
  basePath: string,
  slug: string,
  tab: AppTabKey,
  section: AppTabSection
): string {
  const qs = new URLSearchParams(section.query).toString();
  const href = appTabHref(basePath, slug, tab);
  return qs ? `${href}?${qs}` : href;
}

/**
 * Where a former route lands: the tab's route with the section's query,
 * keeping whatever the old link carried (`?pod=`, `?open=`).
 */
export function redirectTarget(
  slug: string,
  tab: AppTabKey,
  sectionId: string | null,
  params: SearchParams
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((v) => query.append(key, v));
    else if (value !== undefined) query.set(key, value);
  }
  const section = sectionId ? APP_TAB_SECTIONS[tab]?.find((s) => s.id === sectionId) : undefined;
  for (const [key, value] of Object.entries(section?.query ?? {})) query.set(key, value);
  const qs = query.toString();
  const href = appTabHref("/apps", slug, tab);
  return qs ? `${href}?${qs}` : href;
}
