/**
 * Pipelines (spec 44 §5.1): the two tabs' list declarations and the query
 * variables they send. Both fields take `search`, `limit` and `after` and no
 * sort, so the order is the server's, newest first. The list state is in
 * memory: the page's `?tab=` must survive a filter change.
 *
 * Pipelines: Branch has no argument yet and narrows a wider page
 * (`NARROW_LIMIT`). Pipelines record no owner, so Mine is empty.
 *
 * Run history: one pipeline's runs (`pipelineId` is required: the run
 * number is a per-pipeline counter). Status, trigger and Mine (runs you
 * triggered) narrow a wider page the same way.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { PipelineSecret } from "@/graphql/pipelines/pipelines.types";

import type { Pipeline, PipelineRunRow } from "./use-pipelines";

export const NARROW_LIMIT = 100;

const NARROWED = `Keeps the matching runs among the newest ${NARROW_LIMIT}, until the runs query takes filters.`;

export const PIPELINES_LIST: ListDefinition = {
  id: "pipelines.list",
  fields: [{ key: "branch", label: "Default branch" }],
  // The server matches the pipeline name, its repository URL and the app it deploys.
  searchPlaceholder: "Search pipelines, repositories…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ owner: "me" }, [], {
    mineNote: "Pipelines do not record who registered them yet, so Mine is empty.",
  }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export function pipelinesVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    search: q.trim() || null,
    limit: filters.branch ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

export function narrowPipelines(rows: Pipeline[], filters: Record<string, string>): Pipeline[] {
  if (filters.owner) return [];
  return filters.branch ? rows.filter((p) => p.defaultBranch === filters.branch) : rows;
}

const RUN_STATUSES = ["pending", "running", "success", "failure", "cancelled"];
const TRIGGERS = ["push", "manual", "webhook", "schedule"];

export const PIPELINE_RUNS_LIST: ListDefinition = {
  id: "pipelines.runs",
  fields: [
    { key: "status", label: "Status", options: RUN_STATUSES.map((s) => ({ value: s, label: s })) },
    { key: "trigger", label: "Trigger", options: TRIGGERS.map((t) => ({ value: t, label: t })) },
  ],
  // The server matches the triggering ref, the actor and the trigger kind.
  searchPlaceholder: "Search refs, actors…",
  defaultSort: [{ key: "run", dir: "desc" }],
  views: standardViews(
    { triggeredBy: "me" },
    [{ key: "failed", label: "Failed", filters: { status: "failure" }, note: NARROWED }],
    { mineNote: `Mine keeps the runs you triggered among the newest ${NARROW_LIMIT}.` }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

function narrowsRuns(filters: Record<string, string>): boolean {
  return Boolean(filters.status || filters.trigger || filters.triggeredBy);
}

export function runsVariables(
  pipelineId: string | null,
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    pipelineId,
    search: q.trim() || null,
    limit: narrowsRuns(filters) ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

/** `me` is the viewer's username. */
export function narrowRuns(
  rows: PipelineRunRow[],
  filters: Record<string, string>,
  me: string | null
): { rows: PipelineRunRow[]; narrowed: boolean } {
  if (!narrowsRuns(filters)) return { rows, narrowed: false };
  return {
    narrowed: true,
    rows: rows.filter((r) => {
      if (filters.status && r.status !== filters.status) return false;
      if (filters.trigger && r.triggerKind !== filters.trigger) return false;
      if (filters.triggeredBy === "me" && (!me || r.triggerActor !== me)) return false;
      return true;
    }),
  };
}

/**
 * A pipeline's Secrets tab: `astroliftPipelineSecrets` returns every name at
 * once with no arguments, so search, sort and numbered pages run in the
 * client (needsBackend: a Page field). Secrets record no creator, so Mine is
 * empty.
 */
export const PIPELINE_SECRETS_LIST: ListDefinition = {
  id: "pipelines.secrets",
  fields: [],
  searchPlaceholder: "Search secret names…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ createdBy: "me" }, [], {
    mineNote: "Pipeline secrets do not record who set them yet, so Mine is empty.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const PIPELINE_SECRETS_SELECT: SelectRowsSpec<PipelineSecret> = {
  filter: { createdBy: () => false },
  text: (s) => [s.name],
  sort: {
    name: (s) => s.name,
    created: (s) => Date.parse(s.createdAt),
    updated: (s) => Date.parse(s.updatedAt),
  },
  id: (s) => s.id,
};
