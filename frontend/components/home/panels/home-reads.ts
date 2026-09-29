"use client";

/**
 * The reads Home's panels share (rule 2). Each is the exact query and
 * variables another surface already sends, so Apollo answers a panel from
 * the cache that surface filled, and two panels on one layout make one
 * request between them:
 *
 * - deployments: `LIST_DEPLOYMENTS { limit: 100 }`, the approvals queue's
 *   read. Waiting on you, Failing and Recent deployments read it, so a Home
 *   render fetches deployments once. It is the deployments read that
 *   carries `statusReason` and `buildError`, which Failing puts first;
 *   Recent deployments shows its five newest.
 * - agent runs by status: `LIST_AGENT_TASKS_PAGE`, five of one status with
 *   the total. Failing and Failed runs share the failed five.
 * - the fleet: `LIST_AGENT_FLEET { orgId }`, the Agents list's and the
 *   agent frame's read.
 * - my apps: `LIST_MY_APPS_PAGE`, five with the total for My apps and
 *   Traffic & errors; the Agents list's `{ limit: 200 }` for My agents'
 *   join.
 */

import { useQuery } from "@apollo/client/react";

import { LIST_AGENT_FLEET, LIST_AGENT_TASKS_PAGE } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem, AstroliftAgentTask } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_MY_APPS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { useModules } from "@/graphql/user/user.hooks";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

/** Freshness for approvals and deploys, as the approvals queue polls. */
export const HOME_POLL_MS = 30_000;
/** Freshness for runs, as the Runs page polls its first page. */
export const RUNS_POLL_MS = 10_000;

/**
 * What a panel may show inside itself, from the cached `me.modules` and
 * permissions the shell already fetched. The registry decides whether the
 * panel is drawn; this decides which of its sources it reads.
 */
export function usePanelAccess() {
  const modules = useModules();
  const { can } = useMyPermissions();
  return { canView: modules.canView, can };
}

// One empty array per kind, so a hook's rows keep their identity while a
// query has not answered (memos and effects keyed on them stay still).
const NO_DEPLOYMENTS: AstroliftDeployment[] = [];
const NO_TASKS: HomeAgentTask[] = [];
const NO_AGENTS: AstroliftAgentListItem[] = [];
const NO_APPS: AstroliftRegisteredApp[] = [];

/** A query's answer as a panel draws it. */
export interface HomeRead {
  /** First fetch in flight with nothing in hand. */
  loading: boolean;
  /** The query failed with nothing in hand. */
  error: string | null;
  onRetry: () => void;
}

/** A query's loading, error and retry, with `hasData` saying whether it answered. */
export function readState(
  q: { loading: boolean; error?: { message: string } | undefined; refetch: () => Promise<unknown> },
  hasData: boolean
): HomeRead {
  return {
    loading: q.loading && !hasData,
    error: q.error && !hasData ? q.error.message : null,
    onRetry: () => {
      q.refetch().catch(() => {});
    },
  };
}

/**
 * Several sources as one panel state: loading until any rows arrive, and
 * failed only when nothing arrived and a source failed. Retry retries the
 * failed ones.
 */
export function combineReads(sources: HomeRead[], hasRows: boolean): HomeRead {
  const loading = !hasRows && sources.some((s) => s.loading);
  const failed = sources.filter((s) => s.error);
  return {
    loading,
    error: !hasRows && !loading ? (failed[0]?.error ?? null) : null,
    onRetry: () => failed.forEach((s) => s.onRetry()),
  };
}

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

/** The approvals queue's page size, and so the shared deployments read's. */
export const HOME_DEPLOYMENTS_LIMIT = 100;

/**
 * The approvals queue's deployments read: the newest 100, newest created
 * first. `capped` says the read is full, so there may be more than it holds.
 */
export function useHomeDeployments({ skip = false }: { skip?: boolean } = {}) {
  const q = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { limit: HOME_DEPLOYMENTS_LIMIT },
    fetchPolicy: "cache-and-network",
    pollInterval: HOME_POLL_MS,
    skip,
  });
  const data = q.data ?? q.previousData;
  const deployments = data?.astroliftDeployments ?? NO_DEPLOYMENTS;
  return {
    deployments,
    capped: deployments.length >= HOME_DEPLOYMENTS_LIMIT,
    ...readState(q, Boolean(data)),
  };
}

export type HomeAgentTask = Pick<
  AstroliftAgentTask,
  | "id"
  | "agentSlug"
  | "agentName"
  | "projectSlug"
  | "status"
  | "failureMessage"
  | "createdAt"
  | "startedAt"
  | "finishedAt"
>;

interface AgentTasksPageResp {
  agentTasksPage: { items: HomeAgentTask[]; nextCursor: string | null; totalCount: number | null };
}

/** Agent runs of one status (or every status), newest first, with the total. */
export function useHomeAgentRuns(
  status: string | null,
  { limit = 5, skip = false }: { limit?: number; skip?: boolean } = {}
) {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const q = useQuery<AgentTasksPageResp>(LIST_AGENT_TASKS_PAGE, {
    variables: { orgId, status, workloadId: null, search: null, limit, after: null },
    fetchPolicy: "cache-and-network",
    pollInterval: RUNS_POLL_MS,
    skip: skip || !orgId,
  });
  const data = q.data ?? q.previousData;
  return {
    runs: data?.agentTasksPage.items ?? NO_TASKS,
    total: data?.agentTasksPage.totalCount ?? null,
    ...readState(q, Boolean(data)),
    // No org yet is still loading, not empty.
    loading: !skip && (!orgId || (q.loading && !data)),
  };
}

interface FleetResp {
  agentFleet: AstroliftAgentListItem[];
}

/** The org's agents, as the Agents list reads them. */
export function useHomeFleet({ skip = false }: { skip?: boolean } = {}) {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const q = useQuery<FleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    fetchPolicy: "cache-and-network",
    skip: skip || !orgId,
  });
  const data = q.data ?? q.previousData;
  return {
    agents: data?.agentFleet ?? NO_AGENTS,
    ...readState(q, Boolean(data)),
    loading: !skip && (!orgId || (q.loading && !data)),
  };
}

interface MyAppsResp {
  astroliftMyAppsPage: {
    items: AstroliftRegisteredApp[];
    nextCursor: string | null;
    totalCount: number | null;
  };
}

/**
 * The apps the viewer holds a role on. `top` is five with their latest
 * deploy, for My apps and Traffic & errors; `all` is the Agents list's Mine
 * join (200, the backend's page cap), for My agents.
 */
export function useHomeMyApps(size: "top" | "all", { skip = false }: { skip?: boolean } = {}) {
  const variables =
    size === "top"
      ? { includeFreshness: true, search: null, includeArchived: false, limit: 5 }
      : { limit: 200 };
  const q = useQuery<MyAppsResp>(LIST_MY_APPS_PAGE, {
    variables,
    fetchPolicy: "cache-and-network",
    skip,
  });
  const data = q.data ?? q.previousData;
  return {
    apps: data?.astroliftMyAppsPage.items ?? NO_APPS,
    total: data?.astroliftMyAppsPage.totalCount ?? null,
    ...readState(q, Boolean(data)),
  };
}
