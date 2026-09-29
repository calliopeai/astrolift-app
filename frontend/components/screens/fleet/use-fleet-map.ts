"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { AGENT_TASK_TRANSITIONS_SINCE } from "@/graphql/agents/agents.queries";
import type {
  AgentTaskTransition,
  AgentTaskTransitionsSinceData,
} from "@/graphql/agents/agents.types";
import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import type { HeartbeatStatus } from "@/lib/cluster-heartbeat";

import type { FleetClusterLiveness } from "./FleetMap";

// Poll cadence for the transition feed — 4s sits inside the LiveFlowMap 3–5s
// window (#1090/#1091), matching the workflow-run DAG.
const POLL_MS = 4000;
// Cluster liveness changes on the ~30s heartbeat cadence, so poll it slower.
const CLUSTER_POLL_MS = 15000;
// Seed the cursor with a recent lookback so the first frame shows the live
// fleet (agent tasks are ephemeral — minutes, not hours), not ancient history.
const LOOKBACK_MS = 6 * 60 * 60 * 1000;
// Keep settled tasks on the map briefly (throughput context), then drop them so
// the live view stays focused. Non-terminal tasks are never pruned.
const RETENTION_MS = 15 * 60 * 1000;

const TERMINAL = new Set(["completed", "failed", "timed_out", "cancelled"]);

// The heartbeat slice of `astroliftClusters` we read. Typed locally (the
// committed generated cluster type omits these fields — same reason
// lib/cluster-heartbeat.ts types them locally).
interface FleetClusterRow {
  id: string;
  name: string;
  heartbeatStatus: HeartbeatStatus;
  heartbeatAgeSeconds: number | null;
}
interface ClustersLivenessData {
  astroliftClusters: FleetClusterRow[];
}

/**
 * The data half of FleetMapPanel (#1091 — LiveFlowMap P2).
 *
 * Polls `agentTaskTransitionsSince` with a moving `updatedAt` cursor, folds
 * each batch into an accumulated fleet, and hands it to the live dispatch
 * map. Cluster nodes are coloured from the existing clusters-list heartbeat.
 */
export function useFleetMap() {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? null;

  const [since, setSince] = React.useState<string>(() =>
    new Date(Date.now() - LOOKBACK_MS).toISOString()
  );
  const [tasks, setTasks] = React.useState<Map<string, AgentTaskTransition>>(() => new Map());

  const { data, error } = useQuery<AgentTaskTransitionsSinceData>(AGENT_TASK_TRANSITIONS_SINCE, {
    variables: { orgId: orgId ?? "", since, limit: 200 },
    skip: !orgId,
    fetchPolicy: "network-only",
    pollInterval: POLL_MS,
  });

  // Cluster liveness for the cluster layer. The existing clusters list carries
  // heartbeat per cluster in one round-trip — a dynamic number of per-cluster
  // `astroliftClusterLiveState` calls would break rules-of-hooks. Best-effort:
  // a caller without cluster read just sees neutral cluster nodes.
  const { data: clustersData } = useQuery<ClustersLivenessData>(LIST_CLUSTERS, {
    fetchPolicy: "cache-and-network",
    pollInterval: CLUSTER_POLL_MS,
    errorPolicy: "all",
  });

  const clusterLiveness = React.useMemo<FleetClusterLiveness>(() => {
    const m: FleetClusterLiveness = new Map();
    for (const c of clustersData?.astroliftClusters ?? []) {
      m.set(c.id, { status: c.heartbeatStatus, ageSeconds: c.heartbeatAgeSeconds });
    }
    return m;
  }, [clustersData]);

  // Fold each batch of transitions into the accumulated fleet and advance the
  // cursor to the newest `updatedAt` seen (parsed, so it is format-agnostic).
  // Prune settled tasks past the retention window; live tasks are never pruned.
  // The Apollo poll is the external system this effect subscribes to: each
  // completed fetch (v4 removed `onCompleted`, so we react to `data`) folds the
  // batch into accumulated state — the rule's blessed subscribe-and-setState
  // case, not a render-time derivation.
  React.useEffect(() => {
    const incoming = data?.agentTaskTransitionsSince;
    if (!incoming || incoming.length === 0) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTasks((prev) => {
      const next = new Map(prev);
      for (const t of incoming) next.set(t.id, t);
      const cutoff = Date.now() - RETENTION_MS;
      for (const [id, t] of next) {
        if (TERMINAL.has(t.status)) {
          const settled = Date.parse(t.finishedAt ?? t.updatedAt);
          if (!Number.isNaN(settled) && settled < cutoff) next.delete(id);
        }
      }
      return next;
    });

    let maxStr = since;
    let maxMs = Date.parse(since);
    for (const t of incoming) {
      const ms = Date.parse(t.updatedAt);
      if (!Number.isNaN(ms) && ms > maxMs) {
        maxMs = ms;
        maxStr = t.updatedAt;
      }
    }
    if (maxStr !== since) setSince(maxStr);
    // Folds on each completed fetch; `since` is read fresh from the render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const taskList = React.useMemo(() => [...tasks.values()], [tasks]);

  return {
    tasks: taskList,
    clusterLiveness,
    errorMessage: error?.message ?? null,
    // Spinner until the org resolves and the first fetch lands; then either
    // the map or the empty state.
    firstLoad: taskList.length === 0 && !error && (!orgId || !data),
  };
}
