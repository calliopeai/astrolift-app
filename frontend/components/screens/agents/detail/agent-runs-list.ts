/**
 * The agent's Runs tab as a list (spec 44 §4.4, §5.1): the declaration and
 * the pure steps between the page query and the screen.
 *
 * Views: All · Mine · Running · Failed · Queued. `agentTasksPage` takes
 * `workloadId`, one `status`, `search` and a cursor, so the status chip, the
 * status views and search are server side. It has no initiator argument and
 * no sort, and a run carries no "started by", so Mine cannot narrow yet (its
 * note says so) and the list keeps the server's order, newest first, with no
 * sortable columns.
 */
import type { SortState } from "@/components/data-table";
import {
  type ListDefinition,
  type ListFieldOption,
  standardViews,
} from "@/components/list/list-state";

export type RunDot = "ok" | "warn" | "error" | "muted" | "pending";

/** Agent run vocabulary (spec 33 §6); unknown states read muted. */
export const RUN_STATUS_DOT: Record<string, RunDot> = {
  running: "pending",
  queued: "warn",
  pending: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

export function runDot(status: string): RunDot {
  return RUN_STATUS_DOT[status.toLowerCase()] ?? "muted";
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/** A run's wall time, `0:42` or `1:02:03`; null while it has not both started and finished. */
export function runDuration(startedAt: string | null, finishedAt: string | null): string | null {
  if (!startedAt || !finishedAt) return null;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

const STATUS_OPTIONS: ListFieldOption[] = [
  "running",
  "queued",
  "completed",
  "failed",
  "timed_out",
  "cancelled",
].map((s) => ({ value: s, label: titleCase(s) }));

const NEWEST_FIRST: SortState[] = [{ key: "created", dir: "desc" }];

export const AGENT_RUNS_LIST: ListDefinition = {
  id: "agents.detail.runs",
  fields: [{ key: "status", label: "Status", options: STATUS_OPTIONS }],
  searchPlaceholder: "Search run ids…",
  defaultSort: NEWEST_FIRST,
  views: standardViews(
    { startedBy: "me" },
    [
      { key: "running", label: "Running", filters: { status: "running" } },
      { key: "failed", label: "Failed", filters: { status: "failed" } },
      { key: "queued", label: "Queued", filters: { status: "queued" } },
    ],
    {
      mineNote:
        "Mine shows every run of this agent for now: a run does not record who started it yet.",
    }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/**
 * The page query's variables. `startedBy` (Mine) has no server argument and
 * is dropped here.
 */
export function runsPageVariables(
  orgId: string,
  workloadId: string,
  filters: Record<string, string>,
  q: string,
  limit: number,
  after: string | null
) {
  return {
    orgId,
    workloadId,
    status: filters.status || null,
    search: q.trim() || null,
    limit,
    after,
  };
}

/** A running run on a VNC pod that has published its relay path. */
export function canWatchLive(t: { status: string; vncEnabled: boolean; vncUrl: string }): boolean {
  return t.status === "running" && t.vncEnabled && Boolean(t.vncUrl);
}

/** A running headless run, watched through its log tail. */
export function canWatchLogs(t: { status: string; vncEnabled: boolean }): boolean {
  return t.status === "running" && !t.vncEnabled;
}
