import type { DetailTab } from "@/components/DetailPageTabs";
import {
  activeTabSection,
  type DetailTabSection,
  type DetailTabSpec,
  detailRedirectTarget,
  detailTabHref,
  detailTabs,
  resolveDetailTab,
  type SearchParams,
  sectionsBy,
  tabSectionHref,
} from "@/components/detail/detail-tabs-model";

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

/** `label` is an i18n key under `apps.tabs.*`; an empty `segment` is Overview. */
export type AppTabSpec = DetailTabSpec<AppTabKey>;

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
  return detailTabHref(APP_TABS, basePath, slug, tab);
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
  return resolveDetailTab(APP_TABS, basePath, pathname, slug, active);
}

/** The row as `DetailTab`s, labels resolved by the caller. */
export function appTabs(
  basePath: string,
  slug: string,
  pathname: string,
  label: (key: string) => string,
  active?: string
): DetailTab[] {
  return detailTabs(APP_TABS, basePath, slug, pathname, label, active);
}

/** One section inside a tab; `label` is an i18n key under `apps.frame.sections.*`. */
export type AppTabSection = DetailTabSection;

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
  logs: sectionsBy(
    ["logs", "logs"],
    ["metrics", "metrics"],
    ["console", "console"],
    ["commands", "commands"]
  ),
  access: sectionsBy(
    ["members", "members"],
    ["tokens", "tokens"],
    ["security", "security"],
    ["edge", "edge"]
  ),
  settings: sectionsBy(
    ["general", "general"],
    ["configuration", "configuration"],
    ["manifest", "manifest"],
    ["webhooks", "webhooks"],
    ["environments", "environments"],
    ["danger-zone", "dangerZone"]
  ),
};

export type { SearchParams };

/** The section a tab's query selects, or its default. */
export function activeSection(tab: AppTabKey, params: SearchParams): string {
  return activeTabSection(APP_TAB_SECTIONS[tab], params);
}

/** A section's href: the tab's route plus the section's query. */
export function sectionHref(
  basePath: string,
  slug: string,
  tab: AppTabKey,
  section: AppTabSection
): string {
  return tabSectionHref(APP_TABS, basePath, slug, tab, section);
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
  return detailRedirectTarget(APP_TABS, APP_TAB_SECTIONS, "/apps", slug, tab, sectionId, params);
}
