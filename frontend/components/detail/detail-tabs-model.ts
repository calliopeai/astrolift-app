import type { DetailTab } from "@/components/DetailPageTabs";

/**
 * The route model every entity detail frame shares (spec 44 §5.2): one row
 * of tabs, each its own route under `<base>/<slug>`, and the sections a
 * consolidated tab holds, each picked by a query (`?section=`). The routes a
 * tab absorbed (`owns`) redirect into it and still light it up. The app and
 * agent frames each declare their tabs and sections and read them through
 * these functions. Pure.
 */
export interface DetailTabSpec<K extends string = string> {
  key: K;
  /** What the frame's `label` callback resolves: an i18n key, or the text itself. */
  label: string;
  /** The route segment under `<base>/<slug>`; empty for the default tab. */
  segment: string;
  /** Former routes this tab absorbed (each now redirects here). */
  owns: string[];
}

/** One section inside a tab: its query selects it; the first is the default. */
export interface DetailTabSection {
  id: string;
  /** What the sections nav's `label` callback resolves. */
  label: string;
  /** The query that selects it; empty for the tab's default section. */
  query: Record<string, string>;
}

export type SearchParams = Record<string, string | string[] | undefined>;

/** Sections picked by `?section=<id>`, the first being the default. */
export function sectionsBy(...ids: [id: string, label: string][]): DetailTabSection[] {
  return ids.map(
    ([id, label], i): DetailTabSection => ({ id, label, query: i === 0 ? {} : { section: id } })
  );
}

/** `<base>/<slug>` or `<base>/<slug>/<segment>`. */
export function detailTabHref<K extends string>(
  tabs: readonly DetailTabSpec<K>[],
  basePath: string,
  slug: string,
  tab: K
): string {
  const segment = tabs.find((t) => t.key === tab)?.segment ?? "";
  return segment ? `${basePath}/${slug}/${segment}` : `${basePath}/${slug}`;
}

function tabFor<K extends string>(tabs: readonly DetailTabSpec<K>[], name: string): K | undefined {
  return tabs.find(
    (t) => t.key === name || (t.segment && t.segment === name) || t.owns.includes(name)
  )?.key;
}

/**
 * The tab that owns a pathname: the first segment after `<base>/<slug>`
 * picks it, so `/apps/acme/workloads/web` is Workloads. An explicit `active`
 * (a tab key or an absorbed route's name) is the fallback for a path the
 * model does not know; the first tab is the default.
 */
export function resolveDetailTab<K extends string>(
  tabs: readonly DetailTabSpec<K>[],
  basePath: string,
  pathname: string,
  slug: string,
  active?: string
): K {
  const fallback = tabs[0].key;
  const root = `${basePath}/${slug}`;
  if (pathname === root || pathname === `${root}/`) return fallback;
  if (pathname.startsWith(`${root}/`)) {
    const segment = pathname.slice(root.length + 1).split(/[/?#]/)[0];
    const owner = tabFor(tabs, segment);
    if (owner) return owner;
  }
  return (active && tabFor(tabs, active)) || fallback;
}

/** The row as `DetailTab`s, labels resolved by the caller. */
export function detailTabs<K extends string>(
  tabs: readonly DetailTabSpec<K>[],
  basePath: string,
  slug: string,
  pathname: string,
  label: (key: string) => string,
  active?: string
): DetailTab[] {
  const current = resolveDetailTab(tabs, basePath, pathname, slug, active);
  return tabs.map((tab) => ({
    key: tab.key,
    label: label(tab.label),
    href: detailTabHref(tabs, basePath, slug, tab.key),
    active: tab.key === current,
  }));
}

function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** The section a tab's query selects, or its default. */
export function activeTabSection(
  sections: readonly DetailTabSection[] | undefined,
  params: SearchParams
): string {
  const list = sections ?? [];
  const hit = list.find(
    (s) =>
      Object.keys(s.query).length > 0 &&
      Object.entries(s.query).every(([k, v]) => first(params[k]) === v)
  );
  return (hit ?? list[0])?.id ?? "";
}

/** A tab route plus a query. */
export function withQuery(href: string, query: URLSearchParams): string {
  const qs = query.toString();
  return qs ? `${href}?${qs}` : href;
}

/** A section's href: the tab's route plus the section's query. */
export function tabSectionHref<K extends string>(
  tabs: readonly DetailTabSpec<K>[],
  basePath: string,
  slug: string,
  tab: K,
  section: DetailTabSection
): string {
  return withQuery(detailTabHref(tabs, basePath, slug, tab), new URLSearchParams(section.query));
}

/**
 * Where a former route lands: the tab's route with the section's query,
 * keeping whatever else the old link carried (`?pod=`, `?open=`).
 */
export function detailRedirectTarget<K extends string>(
  tabs: readonly DetailTabSpec<K>[],
  sections: Partial<Record<K, DetailTabSection[]>>,
  basePath: string,
  slug: string,
  tab: K,
  sectionId: string | null,
  params: SearchParams
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((v) => query.append(key, v));
    else if (value !== undefined) query.set(key, value);
  }
  const section = sectionId ? sections[tab]?.find((s) => s.id === sectionId) : undefined;
  // A `?section=` the old link carried named one of its own sections, not the target's.
  if (section) query.delete("section");
  for (const [key, value] of Object.entries(section?.query ?? {})) query.set(key, value);
  return withQuery(detailTabHref(tabs, basePath, slug, tab), query);
}
