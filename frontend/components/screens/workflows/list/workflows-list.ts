/**
 * The Workflows list declaration (spec 44 §5.1, §4.4) and the pure steps the
 * hook runs: the join that makes one row per workflow of either kind, then
 * views, filters, sort and numbered pages.
 *
 * One list holds every workflow a person may open: configured workflows (a
 * definition with inputs and a trigger), and definitions run directly
 * (repository pipelines and org-authored ones). Platform templates are the
 * Templates view, read-only, to create a workflow from or clone. Each row
 * opens `/workflows/<slug>`, whose frame resolves either kind.
 *
 * Why this runs here and not on the server: `workflowsPage` searches but
 * takes no filter, sort or page number, `workflowDefinitions` takes none of
 * them, and neither knows the other. The hook reads both once (the
 * configured page at the backend's page cap), plus the newest definition
 * runs for a definition's last run, and `selectWorkflows` answers the list
 * state. When the backend grows the §5.1 contract the hook sends
 * `list.filters`, `sort` and `page` instead and this step goes away.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import type { WorkflowLine } from "@/components/viz/core/workflow-model";
import {
  configuredSubject,
  definitionSubject,
  isFailedState,
  patternLabel,
  type WorkflowFrameRun,
} from "@/components/screens/workflows/detail/workflow-frame-subject";
import { definitionLine } from "@/components/workflows/definition-line";
import type {
  ConfiguredWorkflowWithRuns,
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

/** The patterns the builder offers, in its order; any other value reads as its own words. */
export const PATTERN_LABEL: Record<string, string> = {
  single: "Single",
  chained: "Chained",
  fan_out: "Fan-out",
  supervisor_worker: "Supervisor / worker",
  review_loop: "Review loop",
  advisor: "Advisor",
};

export function workflowPatternLabel(patternKind: string): string {
  return PATTERN_LABEL[patternKind] ?? patternLabel(patternKind);
}

/** How the last run ended, or that there is none. `unknown`: it ran, the list cannot say how. */
export type LastRunKey = "running" | "succeeded" | "failed" | "cancelled" | "never" | "unknown";

export const LAST_RUN_LABEL: Record<LastRunKey, string> = {
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
  never: "Never run",
  unknown: "Ran, status not listed",
};

export const LAST_RUN_DOT: Record<LastRunKey, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  succeeded: "ok",
  failed: "error",
  cancelled: "muted",
  never: "muted",
  unknown: "muted",
};

const LAST_RUN_ORDER: LastRunKey[] = [
  "running",
  "failed",
  "succeeded",
  "cancelled",
  "unknown",
  "never",
];

// ---------------------------------------------------------------------------
// Declaration
// ---------------------------------------------------------------------------

export const WORKFLOWS_LIST: ListDefinition = {
  id: "workflows",
  fields: [
    {
      key: "pattern",
      label: "Pattern",
      options: Object.entries(PATTERN_LABEL).map(([value, label]) => ({ value, label })),
    },
    // Free text: matched on the definition's project slug.
    { key: "project", label: "Project" },
    {
      key: "status",
      label: "Last run",
      options: (["running", "succeeded", "failed", "cancelled", "never"] as LastRunKey[]).map(
        (value) => ({ value, label: LAST_RUN_LABEL[value].toLowerCase() })
      ),
    },
  ],
  searchPlaceholder: "Search workflows, slugs, definitions...",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews(
    { mine: "1" },
    [
      {
        key: "templates",
        label: "Templates",
        filters: { template: "1" },
        note: "Platform templates are read-only: create a workflow from one, or clone it to edit.",
      },
    ],
    {
      mineNote: "Workflows do not record who created them yet, so Mine is empty until they do.",
    }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** `Agents ▾ › Workflows` [› tail]: the first crumb switches between the Agents area's functions. */
export function workflowsCrumbs(...tail: Crumb[]): Crumb[] {
  const area = areaSwitcher(NAV, "agents", "workflows");
  const self: Crumb =
    tail.length > 0 ? { label: "Workflows", href: "/workflows" } : { label: "Workflows" };
  return [area, self, ...tail];
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

/** One row: a configured workflow, a definition run directly, or a platform template. */
export interface WorkflowRow {
  id: string;
  kind: "configured" | "definition" | "template";
  slug: string;
  name: string;
  description: string;
  patternKind: string;
  /** The definition's project; empty when it has none, or it has not loaded. */
  projectSlug: string;
  projectTeamSlug: string;
  /** `owner/repo` path for a repository definition, else null. */
  sourcePath: string | null;
  /** The definition behind a configured workflow; itself for a definition. */
  definitionSlug: string;
  definitionName: string;
  /** Null for a configured workflow whose definition is not visible. */
  stageCount: number | null;
  /** The stage shape, for the card glyph. Empty when the stages are unknown. */
  line: WorkflowLine;
  isEnabled: boolean;
  /** A configured workflow's trigger; null for a definition (it runs by hand). */
  triggerKind: string | null;
  scheduleCron: string | null;
  lastRun: LastRunKey;
  lastRunAt: string | null;
  /** Runs recorded for a configured workflow; null for a definition. */
  runCount: number | null;
  /** Org-owned and not reconciled from a repository, so Delete is offered. */
  deletable: boolean;
  /** Created by the viewer. Always false until workflows record a creator. */
  mine: boolean;
  configured: ConfiguredWorkflowWithRuns | null;
  definition: WorkflowDefinitionSummary | null;
}

function lastRunKey(run: WorkflowFrameRun | null, runCount: number | null): LastRunKey {
  if (!run) return runCount != null && runCount > 0 ? "unknown" : "never";
  if (run.live) return "running";
  if (isFailedState(run.state)) return "failed";
  const s = run.state.toLowerCase();
  if (s.includes("cancel") || s.includes("terminat")) return "cancelled";
  return "succeeded";
}

function isTemplate(d: WorkflowDefinitionSummary): boolean {
  return d.isGlobal || d.organizationGuid == null;
}

function emptyLine(slug: string, name: string): WorkflowLine {
  return { id: `definition:${slug}`, name, stations: [] };
}

/**
 * Configured workflows and the visible definitions into list rows. A
 * configured workflow takes its project, stage count and shape from the
 * definition behind it; a definition takes its last run from `definitionRuns`
 * (the newest page of runs, so a definition that last ran before them reads
 * `unknown`, or `never` when the page holds every run).
 */
export function joinWorkflows(
  configured: readonly ConfiguredWorkflowWithRuns[],
  definitions: readonly WorkflowDefinitionSummary[],
  definitionRuns: readonly WorkflowDefinitionRun[],
  { runsComplete }: { runsComplete: boolean }
): WorkflowRow[] {
  const bySlug = new Map(definitions.map((d) => [d.slug, d]));
  const ranDefinitions = new Set(definitionRuns.map((r) => r.definitionGuid));
  const lineOf = (d: WorkflowDefinitionSummary) =>
    definitionLine(
      d,
      d.stages.map((s) => ({ ...s, agentName: s.agentName || s.agentSlug || s.agentRef }))
    );

  const configuredRows = configured.map((w): WorkflowRow => {
    const d = bySlug.get(w.definitionSlug) ?? null;
    const subject = configuredSubject(w, d);
    return {
      id: `configured:${w.guid}`,
      kind: "configured",
      slug: w.slug,
      name: w.name,
      description: w.description,
      patternKind: subject.patternKind,
      projectSlug: d?.projectSlug ?? "",
      projectTeamSlug: d?.projectTeamSlug ?? "",
      sourcePath: null,
      definitionSlug: w.definitionSlug,
      definitionName: w.definitionName,
      stageCount: subject.stageCount,
      line: d ? lineOf(d) : emptyLine(w.definitionSlug, w.definitionName),
      isEnabled: w.isEnabled,
      triggerKind: w.triggerKind,
      scheduleCron: w.scheduleCron,
      lastRun: lastRunKey(subject.lastRun, w.runCount),
      lastRunAt: subject.lastRun?.startedAt ?? null,
      runCount: w.runCount,
      deletable: true,
      mine: false,
      configured: w,
      definition: d,
    };
  });

  const definitionRows = definitions.map((d): WorkflowRow => {
    const subject = definitionSubject(d, [...definitionRuns]);
    const template = isTemplate(d);
    const ranBeforePage = !runsComplete && !ranDefinitions.has(d.guid);
    return {
      id: `definition:${d.guid}`,
      kind: template ? "template" : "definition",
      slug: d.slug,
      name: d.name,
      description: d.description,
      patternKind: d.patternKind,
      projectSlug: d.projectSlug,
      projectTeamSlug: d.projectTeamSlug,
      sourcePath: d.sourceRepo ? `${d.sourceRepo}/${d.sourcePath}` : null,
      definitionSlug: d.slug,
      definitionName: d.name,
      stageCount: d.stageCount,
      line: lineOf(d),
      isEnabled: d.isEnabled,
      triggerKind: null,
      scheduleCron: null,
      lastRun: subject.lastRun
        ? lastRunKey(subject.lastRun, null)
        : ranBeforePage
          ? "unknown"
          : "never",
      lastRunAt: subject.lastRun?.startedAt ?? null,
      runCount: null,
      deletable: !template && Boolean(d.organizationGuid) && !d.sourceRepo,
      mine: false,
      configured: null,
      definition: d,
    };
  });

  return [...configuredRows, ...definitionRows];
}

// ---------------------------------------------------------------------------
// Select
// ---------------------------------------------------------------------------

type SortValue = string | number;

const lower = (s: string | null | undefined) => (s ?? "").toLowerCase();

const SORT_VALUE: Record<string, (w: WorkflowRow) => SortValue> = {
  name: (w) => w.name.toLowerCase(),
  pattern: (w) => workflowPatternLabel(w.patternKind).toLowerCase(),
  stages: (w) => w.stageCount ?? -1,
  // Never run sorts before the oldest run.
  lastRun: (w) => (w.lastRunAt ? Date.parse(w.lastRunAt) : 0),
  status: (w) => LAST_RUN_ORDER.indexOf(w.lastRun),
};

function matches(w: WorkflowRow, filters: Record<string, string>): boolean {
  // Templates are their own view; every other view is the org's workflows.
  if (filters.template ? w.kind !== "template" : w.kind === "template") return false;
  if (filters.mine && !w.mine) return false;
  if (filters.pattern && w.patternKind !== filters.pattern) return false;
  if (filters.project && lower(w.projectSlug) !== lower(filters.project)) return false;
  if (filters.status && w.lastRun !== lower(filters.status)) return false;
  return true;
}

/** Client-side search until the list query takes one: name, slug, description, definition, project, source. */
function searched(w: WorkflowRow, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  return [w.name, w.slug, w.description, w.definitionName, w.projectSlug, w.sourcePath].some((v) =>
    lower(v).includes(needle)
  );
}

function compare(a: WorkflowRow, b: WorkflowRow, sort: SortState[]): number {
  for (const s of sort) {
    const value = SORT_VALUE[s.key];
    if (!value) continue;
    const x = value(a);
    const y = value(b);
    if (x < y) return s.dir === "asc" ? -1 : 1;
    if (x > y) return s.dir === "asc" ? 1 : -1;
  }
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * One numbered page: view filters and chips applied, searched, sorted and
 * sliced. `totalCount` is the filtered count, for "1-25 of 140".
 */
export function selectWorkflows(
  rows: readonly WorkflowRow[],
  {
    q = "",
    filters,
    sort,
    page,
    pageSize,
  }: {
    q?: string;
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): { rows: WorkflowRow[]; totalCount: number } {
  const kept = rows
    .filter((w) => matches(w, filters) && searched(w, q.trim()))
    .sort((a, b) => compare(a, b, sort));
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
}

// ---------------------------------------------------------------------------
// Former tabs
// ---------------------------------------------------------------------------

/**
 * Where each of the old page's `?tab=` panels lives now (Leo's rule 3: the
 * Workflows page is only the workflows list). Runs are Agents › Runs, which
 * lists workflow runs with their kind; the definitions catalog is this list
 * (its templates are the Templates view).
 */
export const WORKFLOWS_FORMER_TABS: Record<string, string> = {
  workflows: "/workflows",
  running: "/tasks?view=running&kind=workflow",
  history: "/tasks?kind=workflow",
  definitions: "/workflows?view=templates",
};

/** The redirect for an old `/workflows?tab=…` link, or null when there is none. */
export function workflowsFormerTabTarget(
  searchParams: Record<string, string | string[] | undefined>
): string | null {
  const raw = searchParams.tab;
  if (raw === undefined) return null;
  const tab = Array.isArray(raw) ? raw[0] : raw;
  return (tab && WORKFLOWS_FORMER_TABS[tab]) || "/workflows";
}
