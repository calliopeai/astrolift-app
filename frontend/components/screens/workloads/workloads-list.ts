/**
 * Agents › Workloads (spec 44 §4.1, §10.2): the runtime units behind agents,
 * workflows and functions in one list, views All · Mine · Agents ·
 * Workflows · Functions, an app filter, numbered pages. An app's own
 * deployment, statefulset, job and cronjob workloads stay on that app's
 * Workloads tab, so this list never holds them.
 *
 * Why the step runs here and not on the server: `astroliftWorkloadsPage`
 * takes `appSlug`, `search` and a cursor, with no kind, sort or page number,
 * and a workload records no owner. The hook walks every page (the walk the
 * Apps list already runs, so the two share one cache entry), keeps these
 * three kinds, and `selectWorkloads` answers the rest; Mine lists every
 * workload. Each view says so. Pure.
 */
import type { SortState } from "@/components/data-table";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import {
  clientNote,
  lower,
  selectPage,
  withAllNote,
} from "@/components/screens/agents/skills/catalog";
import type { AstroliftWorkload, WorkloadKind } from "@/graphql/registry/registry.types";

/** The kinds the Agents area owns (§10.2). */
export const AREA_KINDS: readonly WorkloadKind[] = ["agent", "workflow", "function"];

export const KIND_LABEL: Record<string, string> = {
  agent: "Agent",
  workflow: "Workflow",
  function: "Function",
};

export function areaWorkloads(all: AstroliftWorkload[]): AstroliftWorkload[] {
  return all.filter((w) => AREA_KINDS.includes(w.kind));
}

/** `2`, `1–4` (autoscaled), or `0–4` for a function that scales to zero. */
export function scaleLabel(w: AstroliftWorkload): string {
  if (w.hpaMaxReplicas != null) return `${w.hpaMinReplicas ?? 0}–${w.hpaMaxReplicas}`;
  return String(w.replicas ?? 0);
}

const NOTE = clientNote("the workload field has no kind or page argument, so every page is read");

export const WORKLOADS_LIST: ListDefinition = {
  id: "agents.workloads",
  fields: [
    // Free text: the owning app's slug.
    { key: "app", label: "App" },
  ],
  searchPlaceholder: "Search workloads, slugs, apps…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: withAllNote(
    standardViews(
      {},
      [
        { key: "agents", label: "Agents", filters: { kind: "agent" }, note: NOTE },
        { key: "workflows", label: "Workflows", filters: { kind: "workflow" }, note: NOTE },
        { key: "functions", label: "Functions", filters: { kind: "function" }, note: NOTE },
      ],
      { mineNote: `Mine lists every workload until workloads record who owns them. ${NOTE}` }
    ),
    NOTE
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export function selectWorkloads(
  workloads: AstroliftWorkload[],
  q: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  return selectPage(
    workloads,
    {
      matches: (w, f) => {
        if (f.kind && w.kind !== f.kind) return false;
        if (f.app && lower(w.registeredAppSlug) !== lower(f.app)) return false;
        return true;
      },
      text: (w) => [w.name, w.slug, w.registeredAppSlug],
      sortValue: {
        name: (w) => lower(w.name),
        kind: (w) => w.kind,
        app: (w) => lower(w.registeredAppSlug),
      },
      id: (w) => w.id,
    },
    q
  );
}
