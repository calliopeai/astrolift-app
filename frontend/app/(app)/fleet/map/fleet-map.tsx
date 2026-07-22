"use client";

import {
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import { BotIcon, ListOrderedIcon, RadioTowerIcon, ServerIcon } from "lucide-react";
import * as React from "react";

import { FlowGraph, rankLayout } from "@/components/viz";
import type { AgentTaskTransition, FleetTaskDispatcher } from "@/graphql/agents/agents.types";
import {
  formatHeartbeatAge,
  heartbeatPresentation,
  isClusterLive,
  type HeartbeatStatus,
} from "@/lib/cluster-heartbeat";
import { cn } from "@/lib/utils";

// ─── node model ─────────────────────────────────────────────────────────────

type FleetTone = "success" | "running" | "pending" | "danger" | "muted";
type FleetNodeKind = "dispatcher" | "queue" | "cluster" | "agent";

interface FleetNodeData extends Record<string, unknown> {
  /** Stable node id, referenced by other nodes' `needs`. */
  id: string;
  kind: FleetNodeKind;
  label: string;
  sublabel?: string;
  tone: FleetTone;
  /** Live node — pulses the status dot + the incoming edge. */
  pulse?: boolean;
  /** Upstream node ids (→ incoming edges), chained layer-to-layer. */
  needs: string[];
}

/** clusterGuid → live heartbeat snapshot, from `astroliftClusters`. */
export type FleetClusterLiveness = Map<
  string,
  { status: HeartbeatStatus; ageSeconds: number | null }
>;

// The status tokens (#A1) — same palette PipelineDag uses, so the fleet map
// reads identically to the workflow-run DAG. All token-referencing (no raw
// values), so the no-raw-design-values guardrail stays green.
const TONE_RING: Record<FleetTone, string> = {
  success: "ring-success bg-success/5",
  running: "ring-info bg-info/5",
  pending: "ring-warning bg-warning/5",
  danger: "ring-danger-border bg-danger/5",
  muted: "ring-muted-foreground/20 bg-muted/40",
};

const TONE_DOT: Record<FleetTone, string> = {
  success: "bg-success",
  running: "bg-info",
  pending: "bg-warning",
  danger: "bg-danger",
  muted: "bg-muted-foreground/40",
};

const KIND_ICON: Record<FleetNodeKind, React.ComponentType<{ className?: string }>> = {
  dispatcher: RadioTowerIcon,
  queue: ListOrderedIcon,
  cluster: ServerIcon,
  agent: BotIcon,
};

// AgentTask.status → tone + whether the node is live (pulsing).
function agentTone(status: string): { tone: FleetTone; pulse: boolean } {
  switch (status) {
    case "completed":
      return { tone: "success", pulse: false };
    case "running":
      return { tone: "running", pulse: true };
    case "provisioning":
      return { tone: "pending", pulse: true };
    case "queued":
      return { tone: "pending", pulse: false };
    case "failed":
    case "timed_out":
      return { tone: "danger", pulse: false };
    // draft, cancelled, unknown
    default:
      return { tone: "muted", pulse: false };
  }
}

// Cluster heartbeat → tone, reusing the shared heartbeat presentation's dot so
// the map agrees with the Clusters list / Status tab on what "live" looks like.
function clusterTone(status: HeartbeatStatus): FleetTone {
  switch (heartbeatPresentation(status).dot) {
    case "ok":
      return "success";
    case "warn":
      return "pending";
    case "error":
      return "danger";
    default:
      return "muted";
  }
}

function formatDuration(startedAt: string | null, finishedAt: string | null): string | null {
  if (!startedAt) return null;
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) return null;
  const end = finishedAt ? Date.parse(finishedAt) : NaN;
  if (Number.isNaN(end)) return "running";
  const s = Math.max(0, Math.round((end - start) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

function agentSublabel(t: AgentTaskTransition): string {
  const status = t.status.replace(/_/g, " ");
  const titled = status.charAt(0).toUpperCase() + status.slice(1);
  const dur = formatDuration(t.startedAt, t.finishedAt);
  return dur ? `${titled} · ${dur}` : titled;
}

// ─── node renderer ────────────────────────────────────────────────────────────

function FleetNode({ data }: NodeProps<Node<FleetNodeData>>) {
  const Icon = KIND_ICON[data.kind];
  return (
    <div
      className={cn(
        "viz-node bg-background max-w-[224px] min-w-[176px] rounded-md border p-3 shadow-sm ring-1",
        TONE_RING[data.tone]
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground/30" />
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground/30" />
      <div className="flex items-start gap-2">
        <div className="bg-muted mt-0.5 rounded-sm p-1">
          <Icon className="text-muted-foreground size-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "viz-node-dot size-1.5 shrink-0 rounded-full",
                TONE_DOT[data.tone],
                data.pulse && "animate-pulse"
              )}
            />
            <div className="truncate text-sm leading-tight font-medium">{data.label}</div>
          </div>
          {data.sublabel && (
            <div className="text-muted-foreground text-2xs mt-0.5 truncate">{data.sublabel}</div>
          )}
        </div>
      </div>
    </div>
  );
}

const NODE_TYPES: NodeTypes = { fleet: FleetNode };

// ─── graph builder ────────────────────────────────────────────────────────────

const DISPATCH_SERVICE_ID = "dispatcher:__service__";
const QUEUE_ID = "queue";

/**
 * Fold the live task set + cluster liveness into a ranked, layered graph:
 *
 *   dispatcher(s) → task queue → cluster(s) → per-task agent nodes
 *
 * Each layer `needs` the previous one, so `rankLayout` lays it out
 * left-to-right by dependency depth (the same longest-path layout PipelineDag
 * uses). A queued/unrouted task hangs off the queue until the Controller
 * places it on a cluster; a running task hangs off its dispatcher's cluster.
 */
function buildFleetGraph(
  tasks: AgentTaskTransition[],
  clusterLiveness: FleetClusterLiveness
): { nodes: Node<FleetNodeData>[]; edges: Edge[] } {
  const descriptors: FleetNodeData[] = [];
  const dispatcherNodeId = (id: string) => `dispatcher:${id}`;
  const clusterNodeId = (id: string) => `cluster:${id}`;

  // Dispatcher layer — distinct dispatchers observed across the fleet.
  const dispatchers = new Map<string, FleetTaskDispatcher>();
  for (const t of tasks) if (t.dispatcher) dispatchers.set(t.dispatcher.id, t.dispatcher);
  if (dispatchers.size === 0) {
    // Nothing routed yet — anchor the pipeline with the dispatch service.
    descriptors.push({
      id: DISPATCH_SERVICE_ID,
      kind: "dispatcher",
      label: "Dispatch Service",
      tone: "muted",
      needs: [],
    });
  } else {
    for (const d of dispatchers.values()) {
      const loc = [d.cloud, d.region].filter(Boolean).join(" · ");
      descriptors.push({
        id: dispatcherNodeId(d.id),
        kind: "dispatcher",
        label: d.name || d.slug || "Dispatcher",
        sublabel: loc || undefined,
        tone: "muted",
        needs: [],
      });
    }
  }
  const dispatcherIds = descriptors.map((n) => n.id);

  // Queue layer — the funnel; queued/draft tasks are what wait here.
  const queuedCount = tasks.filter((t) => t.status === "queued" || t.status === "draft").length;
  descriptors.push({
    id: QUEUE_ID,
    kind: "queue",
    label: "Task Queue",
    sublabel: `${queuedCount} queued`,
    tone: queuedCount > 0 ? "pending" : "muted",
    needs: dispatcherIds,
  });

  // Cluster layer — distinct clusters the tasks' dispatchers spawn onto,
  // coloured by heartbeat liveness.
  const clusters = new Map<string, string>();
  for (const t of tasks) {
    if (t.dispatcher?.clusterId)
      clusters.set(t.dispatcher.clusterId, t.dispatcher.clusterName || t.dispatcher.clusterId);
  }
  for (const [clusterId, name] of clusters) {
    const live = clusterLiveness.get(clusterId);
    const status = live?.status ?? "never_seen";
    const age = formatHeartbeatAge(live?.ageSeconds ?? null);
    const pres = heartbeatPresentation(status);
    descriptors.push({
      id: clusterNodeId(clusterId),
      kind: "cluster",
      label: name,
      sublabel: age ? `${pres.label} · ${age}` : pres.label,
      tone: clusterTone(status),
      pulse: isClusterLive(status),
      needs: [QUEUE_ID],
    });
  }

  // Agent layer — one node per task, on its cluster (or the queue when it
  // hasn't been placed yet).
  for (const t of tasks) {
    const { tone, pulse } = agentTone(t.status);
    const clusterId = t.dispatcher?.clusterId ?? null;
    const parent = clusterId && clusters.has(clusterId) ? clusterNodeId(clusterId) : QUEUE_ID;
    descriptors.push({
      id: `agent:${t.id}`,
      kind: "agent",
      label: t.podName || `task ${t.id.slice(0, 8)}`,
      sublabel: agentSublabel(t),
      tone,
      pulse,
      needs: [parent],
    });
  }

  const ids = descriptors.map((n) => n.id);
  const idSet = new Set(ids);
  const rankEdges = descriptors.flatMap((n) =>
    (n.needs ?? []).filter((dep) => idSet.has(dep)).map((dep) => ({ source: dep, target: n.id }))
  );
  const pos = rankLayout(ids, rankEdges);
  const byId = new Map(descriptors.map((n) => [n.id, n]));

  const edges: Edge[] = rankEdges.map((e) => ({
    id: `${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
    // Light the edge into any live node (running/provisioning agent, live
    // cluster) with the telemetry flow.
    animated: !!byId.get(e.target)?.pulse,
  }));
  const nodes: Node<FleetNodeData>[] = descriptors.map((n) => ({
    id: n.id,
    type: "fleet",
    position: pos.get(n.id) ?? { x: 0, y: 0 },
    data: n,
  }));
  return { nodes, edges };
}

// ─── public component ─────────────────────────────────────────────────────────

export interface FleetMapProps {
  tasks: AgentTaskTransition[];
  clusterLiveness: FleetClusterLiveness;
  height?: number | string;
}

/**
 * The live agent-fleet dispatch map (#1091 — LiveFlowMap P2).
 *
 * Built on the shared `FlowGraph` telemetry substrate (the same one behind the
 * workflow-run DAG) with a fleet-specific node renderer. `FlowGraph` reads its
 * graph once on mount, so a signature `key` of every node's `id:status` (+
 * cluster liveness) remounts it to re-flow on each transition — the animated
 * edges then flow into the newly-active nodes. Reduced-motion falls back to
 * static status colours via the globals.css media query.
 */
export function FleetMap({ tasks, clusterLiveness, height = 560 }: FleetMapProps) {
  const { nodes, edges } = React.useMemo(
    () => buildFleetGraph(tasks, clusterLiveness),
    [tasks, clusterLiveness]
  );

  // Remount signature: any status change, new task, or cluster-liveness change
  // re-flows the graph.
  const signature = React.useMemo(() => {
    const taskSig = tasks
      .map((t) => `${t.id}:${t.status}`)
      .sort()
      .join("|");
    const clusterSig = [...clusterLiveness.entries()]
      .map(([id, l]) => `${id}:${l.status}`)
      .sort()
      .join("|");
    return `${taskSig}||${clusterSig}`;
  }, [tasks, clusterLiveness]);

  return (
    <FlowGraph
      key={signature}
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      height={height}
      showMiniMap={false}
      variant="telemetry"
      fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
    />
  );
}
