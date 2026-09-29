/**
 * An app's Workloads tab as a list (spec 44 §5.1, §10.2): the app's
 * deployment, statefulset, job and cronjob workloads, filtered by kind.
 * Scheduled jobs fold in as the cronjob kind: `/apps/<slug>/jobs` lands on
 * `?kind=cronjob`, which is this list's kind chip.
 *
 * Why the filter, sort and page step runs here: `astroliftWorkloadsPage`
 * takes `search` and a cursor only, with no kind filter and no sort. An
 * app's workloads are the few its manifest declares, and `astroliftWorkloads`
 * returns them all, so the hook reads the whole set and this answers the
 * rest exactly, with numbered pages. Pure.
 */
import type { SortState } from "@/components/data-table";
import type { ListDefinition } from "@/components/list/list-state";
import type { AstroliftWorkload, WorkloadKind } from "@/graphql/registry/registry.types";

export const KIND_LABEL: Record<WorkloadKind, string> = {
  deployment: "Deployment",
  statefulset: "StatefulSet",
  job: "Job",
  cronjob: "CronJob",
  task: "Task",
  agent: "Agent",
  workflow: "Workflow",
  function: "Function",
};

/** The kinds an app's own Workloads tab holds (spec 44 §10.2). */
export const APP_WORKLOAD_KINDS: WorkloadKind[] = ["deployment", "statefulset", "job", "cronjob"];

export const APP_WORKLOADS_LIST: ListDefinition = {
  id: "apps.workloads",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: APP_WORKLOAD_KINDS.map((k) => ({ value: k, label: KIND_LABEL[k] })),
    },
    {
      key: "exposure",
      label: "Exposure",
      options: [
        { value: "public", label: "Public" },
        { value: "internal", label: "Internal" },
      ],
    },
  ],
  searchPlaceholder: "Search workloads, slugs, kinds…",
  defaultSort: [{ key: "name", dir: "asc" }],
  // An app's workloads have no owner, so there is no Mine to offer: one view,
  // and the list draws no view picker.
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

function compare(a: AstroliftWorkload, b: AstroliftWorkload, key: string): number {
  switch (key) {
    case "kind":
      return a.kind.localeCompare(b.kind);
    case "replicas":
      return (a.replicas || 0) - (b.replicas || 0);
    case "name":
    default:
      return a.name.localeCompare(b.name) || a.slug.localeCompare(b.slug);
  }
}

/** Filter, search, sort and page the whole set. Stable: ties break on the slug. */
export function selectWorkloads(
  all: AstroliftWorkload[],
  filters: Record<string, string>,
  q: string,
  sort: SortState[],
  page: number,
  pageSize: number
): { rows: AstroliftWorkload[]; totalCount: number } {
  const needle = q.trim().toLowerCase();
  const matched = all.filter((w) => {
    if (filters.kind && w.kind !== filters.kind) return false;
    if (filters.exposure === "public" && !w.isPublic) return false;
    if (filters.exposure === "internal" && w.isPublic) return false;
    if (!needle) return true;
    return [w.name, w.slug, w.kind, w.schedule ?? ""].some((f) => f.toLowerCase().includes(needle));
  });
  const sorted = [...matched].sort((a, b) => {
    for (const s of sort) {
      const c = compare(a, b, s.key);
      if (c !== 0) return s.dir === "asc" ? c : -c;
    }
    return a.slug.localeCompare(b.slug);
  });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: sorted.slice(start, start + pageSize), totalCount: matched.length };
}
