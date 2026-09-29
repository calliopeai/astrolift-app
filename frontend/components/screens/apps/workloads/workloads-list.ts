/**
 * An app's Workloads tab as a list (spec 44 §5.1, §10.2): the app's
 * deployment, statefulset, job and cronjob workloads, filtered by kind.
 * Scheduled jobs fold in as the cronjob kind: `/apps/<slug>/jobs` lands on
 * `?kind=cronjob`, which is this list's kind chip.
 *
 * `astroliftWorkloadsPage` answers all of it (#2155): `kinds` holds the
 * list to those four, the kind and exposure chips go through its `filter`,
 * search, the column sort and numbered pages through the §5.1 arguments.
 * Pure.
 */
import type { SortState } from "@/components/data-table";
import { formatSort, type ListDefinition } from "@/components/list/list-state";
import type { WorkloadKind } from "@/graphql/registry/registry.types";

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

/** The page query's variables for a list state; the route preloads the default page with them. */
export function appWorkloadsVariables(
  appSlug: string,
  {
    q,
    filters,
    sort,
    page,
    pageSize,
  }: {
    q: string;
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  const filter: { kind?: string[]; isPublic?: boolean } = {};
  if (filters.kind) filter.kind = [filters.kind];
  if (filters.exposure === "public") filter.isPublic = true;
  if (filters.exposure === "internal") filter.isPublic = false;
  return {
    appSlug,
    kinds: APP_WORKLOAD_KINDS,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    sort: formatSort(sort),
    page: Math.max(1, page),
    pageSize,
  };
}
