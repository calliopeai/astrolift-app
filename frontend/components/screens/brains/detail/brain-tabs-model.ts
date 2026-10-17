import type { DetailTab } from "@/components/DetailPageTabs";
import {
  activeTabSection,
  type DetailTabSection,
  type DetailTabSpec,
  detailTabHref,
  detailTabs,
  resolveDetailTab,
  type SearchParams,
  sectionsBy,
  tabSectionHref,
} from "@/components/detail/detail-tabs-model";

/**
 * A semantic layer's one row of tabs (spec 44 §5.2, #2427): Overview at
 * `/brains/[slug]`, then Substrate, Sharing, Apps, Connect and Settings, each
 * its own route. Labels are keys under `brains.tabs`. Pure.
 */
export type BrainTabKey = "overview" | "substrate" | "sharing" | "apps" | "connect" | "settings";

export const BRAIN_BASE = "/brains";

export const BRAIN_TABS: readonly DetailTabSpec<BrainTabKey>[] = [
  { key: "overview", label: "overview", segment: "", owns: [] },
  { key: "substrate", label: "substrate", segment: "substrate", owns: [] },
  { key: "sharing", label: "sharing", segment: "sharing", owns: [] },
  { key: "apps", label: "apps", segment: "apps", owns: [] },
  { key: "connect", label: "connect", segment: "connect", owns: [] },
  { key: "settings", label: "settings", segment: "settings", owns: [] },
];

export type SharingSection = "teams" | "history";
export type SettingsSection = "rebuild" | "danger-zone";

/** Sharing holds the teams and the access history, Settings the rebuild and the danger zone. */
export const BRAIN_TAB_SECTIONS: Partial<Record<BrainTabKey, DetailTabSection[]>> = {
  sharing: sectionsBy(["teams", "teams"], ["history", "history"]),
  settings: sectionsBy(["rebuild", "rebuild"], ["danger-zone", "dangerZone"]),
};

/** The slug as it sits in a pathname (`usePathname` is encoded). */
const enc = (slug: string) => encodeURIComponent(slug);

export function brainHref(slug: string, tab: BrainTabKey = "overview"): string {
  return detailTabHref(BRAIN_TABS, BRAIN_BASE, enc(slug), tab);
}

export function resolveBrainTab(pathname: string, slug: string): BrainTabKey {
  return resolveDetailTab(BRAIN_TABS, BRAIN_BASE, pathname, enc(slug));
}

/** The row as `DetailTab`s, labels resolved by the caller. */
export function brainTabs(
  slug: string,
  pathname: string,
  label: (key: string) => string
): DetailTab[] {
  return detailTabs(BRAIN_TABS, BRAIN_BASE, enc(slug), pathname, label);
}

/** The section a tab's query selects, or its default. */
export function activeBrainSection(tab: BrainTabKey, params: SearchParams): string {
  return activeTabSection(BRAIN_TAB_SECTIONS[tab], params);
}

/** A section's link; the tab's own route for an id the tab does not have. */
export function brainSectionHref(slug: string, tab: BrainTabKey, sectionId: string): string {
  const section = BRAIN_TAB_SECTIONS[tab]?.find((s) => s.id === sectionId);
  return section
    ? tabSectionHref(BRAIN_TABS, BRAIN_BASE, enc(slug), tab, section)
    : brainHref(slug, tab);
}

/** The sections nav of a tab, labels resolved by the caller. */
export function brainSections(
  slug: string,
  tab: BrainTabKey,
  label: (key: string) => string
): { id: string; label: string; href: string }[] {
  return (BRAIN_TAB_SECTIONS[tab] ?? []).map((s) => ({
    id: s.id,
    label: label(s.label),
    href: brainSectionHref(slug, tab, s.id),
  }));
}

export type { SearchParams };
