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
 * The workflow detail's one row of tabs, named by function (spec 44 §5.2):
 * Builder · Runs · Triggers · Settings. Builder is the default and sits at
 * `/workflows/[slug]`; each other tab is its own route. The former BROCS
 * pillar pages (`/build`, `/run`, `/observe`) and the standalone `/builder`
 * redirect into the tab that took them, and still light it up. Labels are the
 * text itself: the workflows detail has no i18n namespace yet.
 */
export type WorkflowTabKey = "builder" | "runs" | "triggers" | "settings";

export const WORKFLOW_BASE = "/workflows";

export const WORKFLOW_TABS: readonly DetailTabSpec<WorkflowTabKey>[] = [
  { key: "builder", label: "Builder", segment: "", owns: ["build", "builder"] },
  { key: "runs", label: "Runs", segment: "runs", owns: ["run", "observe"] },
  { key: "triggers", label: "Triggers", segment: "triggers", owns: [] },
  { key: "settings", label: "Settings", segment: "settings", owns: [] },
];

/** Settings holds General and the one Danger zone, one at a time by `?section=`. */
export const WORKFLOW_TAB_SECTIONS: Partial<Record<WorkflowTabKey, DetailTabSection[]>> = {
  settings: sectionsBy(["general", "General"], ["danger-zone", "Danger zone"]),
};

/** Every former route under `/workflows/[slug]`, and where it lands now. */
export const WORKFLOW_FORMER_ROUTES: Record<
  string,
  { tab: WorkflowTabKey; section: string | null }
> = {
  build: { tab: "builder", section: null },
  builder: { tab: "builder", section: null },
  run: { tab: "runs", section: null },
  observe: { tab: "runs", section: null },
};

/** The slug as it sits in a pathname (`usePathname` is encoded). */
const enc = (slug: string) => encodeURIComponent(slug);

/** `/workflows/<slug>` or `/workflows/<slug>/<segment>`. */
export function workflowTabHref(slug: string, tab: WorkflowTabKey): string {
  return detailTabHref(WORKFLOW_TABS, WORKFLOW_BASE, enc(slug), tab);
}

/** The tab that owns a pathname; Builder is the default. */
export function resolveWorkflowTab(pathname: string, slug: string): WorkflowTabKey {
  return resolveDetailTab(WORKFLOW_TABS, WORKFLOW_BASE, pathname, enc(slug));
}

/** The row as `DetailTab`s. */
export function workflowTabs(slug: string, pathname: string): DetailTab[] {
  return detailTabs(WORKFLOW_TABS, WORKFLOW_BASE, enc(slug), pathname, (label) => label);
}

/** The section a tab's query selects, or its default. */
export function activeWorkflowSection(tab: WorkflowTabKey, params: SearchParams): string {
  return activeTabSection(WORKFLOW_TAB_SECTIONS[tab], params);
}

/** A section's href by id; the tab's own route for an unknown id. */
export function workflowSectionHref(slug: string, tab: WorkflowTabKey, sectionId: string): string {
  const section = WORKFLOW_TAB_SECTIONS[tab]?.find((s) => s.id === sectionId);
  return section
    ? tabSectionHref(WORKFLOW_TABS, WORKFLOW_BASE, enc(slug), tab, section)
    : workflowTabHref(slug, tab);
}

/** Where a former route lands, keeping whatever query the old link carried (`?run=`). */
export function workflowRedirectTarget(route: string, slug: string, params: SearchParams): string {
  const target = WORKFLOW_FORMER_ROUTES[route] ?? { tab: "builder", section: null };
  return detailRedirectTarget(
    WORKFLOW_TABS,
    WORKFLOW_TAB_SECTIONS,
    WORKFLOW_BASE,
    enc(slug),
    target.tab,
    target.section,
    params
  );
}

export type { SearchParams };
