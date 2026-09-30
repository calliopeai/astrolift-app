/**
 * Apps › Deployments (spec 44 §4.4, §5.1, §5.5): the list declaration, the
 * query variables it sends, and the pure run view of one deployment.
 *
 * The list: views All · Mine · Waiting approval · Failed · Today, cursor
 * paged, live. Everything goes to `astroliftDeploymentsPage`: app,
 * environment, status and search as its own arguments, and trigger, Mine
 * (who triggered it) and since (Today) through its `filter`, with the
 * list's order as `sort` (#2155).
 */
import type { SortState } from "@/components/data-table";
import type { ListDefinition } from "@/components/list/list-state";
import { formatSort, standardViews } from "@/components/list/list-state";
import type { TimelineStep } from "@/components/run/Timeline";
import type { LogLine } from "@/components/run/LogView";
import type { PanelFailure } from "@/components/panel/Panel";
import type {
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
  DeploymentStatus,
  TriggerKind,
} from "@/graphql/lifecycle/lifecycle.types";

import { SINCE_WITH_TODAY, sinceIso } from "./apps-area";
import { IN_FLIGHT } from "./deployments-format";
import { spanMs } from "./run-support";

const STATUSES: DeploymentStatus[] = [
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
  "running",
  "failed",
  "rolled_back",
  "superseded",
];

const TRIGGERS: TriggerKind[] = ["push", "manual", "ci", "scheduled", "rollback", "promotion"];

export const DEPLOYMENTS_LIST: ListDefinition = {
  id: "apps.deployments",
  fields: [
    { key: "app", label: "App" },
    { key: "environment", label: "Environment" },
    {
      key: "status",
      label: "Status",
      options: STATUSES.map((s) => ({ value: s, label: s.replace(/_/g, " ") })),
    },
    { key: "trigger", label: "Trigger", options: TRIGGERS.map((t) => ({ value: t, label: t })) },
    { key: "since", label: "Since", options: SINCE_WITH_TODAY },
  ],
  // The server matches app, environment, branch, image tag and commit.
  searchPlaceholder: "Search apps, branches, tags, commits…",
  defaultSort: [{ key: "started", dir: "desc" }],
  views: standardViews({ triggeredBy: "me" }, [
    { key: "waiting", label: "Waiting approval", filters: { status: "pending_approval" } },
    { key: "failed", label: "Failed", filters: { status: "failed" } },
    { key: "today", label: "Today", filters: { since: "today" } },
  ]),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** The filter input's shape on `astroliftDeploymentsPage` (AstroliftDeploymentsFilter). */
export interface DeploymentsFilter {
  triggeredBy?: string[];
  triggerKind?: string[];
  startedAfter?: string;
}

export interface DeploymentsVariables {
  appSlug: string | null;
  environmentName: string | null;
  statuses: string[] | null;
  search: string | null;
  filter: DeploymentsFilter | null;
  sort: string;
  limit: number;
  after: string | null;
}

/**
 * What `astroliftDeploymentsPage` is sent for a list state. `now` places
 * Today's midnight and the since windows; the hook fixes it per visit.
 */
export function deploymentsVariables(
  filters: Record<string, string>,
  {
    q,
    sort,
    pageSize,
    after,
  }: { q: string; sort: SortState[]; pageSize: number; after: string | null },
  now: number
): DeploymentsVariables {
  const filter: DeploymentsFilter = {};
  if (filters.triggeredBy) filter.triggeredBy = [filters.triggeredBy];
  if (filters.trigger) filter.triggerKind = [filters.trigger];
  const since = sinceIso(filters.since, now);
  if (since) filter.startedAfter = since;
  return {
    appSlug: filters.app || null,
    environmentName: filters.environment || null,
    statuses: filters.status ? [filters.status] : null,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    sort: formatSort(sort),
    limit: pageSize,
    after,
  };
}

// ---------------------------------------------------------------------------
// One deployment as a run (spec 44 §5.5)
// ---------------------------------------------------------------------------

/** The short name of a deploy: its commit, else its id, eight characters. */
export function deploySha(d: Pick<AstroliftDeployment, "commitSha" | "id">): string {
  return (d.commitSha || d.id).slice(0, 8);
}

const ENDED_BADLY = new Set(["failed", "rolled_back"]);
function lastFailure(log: AstroliftDeploymentLogEntry[]) {
  return log.findLast((e) => ENDED_BADLY.has(e.status));
}

/** Phase timing comes from persisted runtime observations, including retries. */
export function deploymentSteps(
  d: AstroliftDeployment,
  _log: AstroliftDeploymentLogEntry[],
  now: number
): TimelineStep[] {
  const phases = new Map((d.phases ?? []).map((p) => [p.name, p]));
  const steps = ["build", "push", "apply", "rollout", "health"].map((name): TimelineStep => {
    const phase = phases.get(name);
    const failed =
      phase?.failedAt &&
      (!phase.completedAt || Date.parse(phase.failedAt) > Date.parse(phase.completedAt));
    const end = failed ? phase.failedAt : (phase?.healthyAt ?? phase?.completedAt);
    const state = failed
      ? "failed"
      : end
        ? "ok"
        : phase?.startedAt && IN_FLIGHT.has(d.status)
          ? "running"
          : "pending";
    return {
      id: name,
      name,
      state,
      durationMs: phase?.startedAt
        ? spanMs(phase.startedAt, end ?? (state === "running" ? now : null))
        : null,
      detail: !phase
        ? "No phase timing recorded"
        : name === "push" && !phase.startedAt
          ? "Push confirmed; separate start unavailable"
          : undefined,
    };
  });
  if (d.approvalsRequired > 0) {
    steps.splice(2, 0, {
      id: "approval",
      name: "approval",
      state:
        d.status === "pending_approval"
          ? "running"
          : d.approvalsReceived >= d.approvalsRequired
            ? "ok"
            : "pending",
      detail: `${d.approvalsReceived}/${d.approvalsRequired} approvals`,
    });
  }
  return steps;
}

/** Why a failed deploy failed, for the first thing on the page (spec 44 §5.2). */
export function deploymentFailure(
  d: AstroliftDeployment,
  log: AstroliftDeploymentLogEntry[]
): PanelFailure | null {
  if (d.status !== "failed") return null;
  if (d.abortedReason) return { title: "Deployment aborted", reason: d.abortedReason };
  // The same order the app's latest-deploy panel reads them in.
  const reason =
    d.statusReason ||
    d.buildError ||
    lastFailure(log)?.message ||
    "No reason was recorded for this failure.";
  return { title: isBuildFailure(d, log) ? "Build failed" : "Deployment failed", reason };
}

/** A build error on a deploy that never reached a rollout: the image never existed. */
function isBuildFailure(d: AstroliftDeployment, log: AstroliftDeploymentLogEntry[]): boolean {
  return (
    d.status === "failed" &&
    Boolean(d.buildError) &&
    !log.some((e) => ["deploying", "redeploying"].includes(e.status))
  );
}

const LEVEL: Partial<Record<string, LogLine["level"]>> = {
  failed: "error",
  rolled_back: "warn",
  superseded: "debug",
};

/** The lifecycle log as log lines: the transition and its message, then any detail. */
export function deploymentLogLines(log: AstroliftDeploymentLogEntry[]): LogLine[] {
  return log.flatMap((e) => {
    const status = e.phase ? `${e.phase}/${e.event}` : e.status.replace(/_/g, " ");
    const head: LogLine = {
      ts: e.occurredAt,
      message: e.message ? `${status}: ${e.message}` : status,
      level: LEVEL[e.status] ?? LEVEL[e.event] ?? "info",
    };
    const detail =
      e.detail && typeof e.detail === "object" && Object.keys(e.detail).length > 0
        ? [{ ts: e.occurredAt, message: JSON.stringify(e.detail), level: "debug" as const }]
        : [];
    return [head, ...detail];
  });
}
