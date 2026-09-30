"use client";

import {
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import {
  BotIcon,
  Loader2Icon,
  RadioIcon,
  RadioTowerIcon,
  ShieldIcon,
  WaypointsIcon,
  WrenchIcon,
} from "lucide-react";
import * as React from "react";

import { FlowGraph, rankLayout } from "@/components/viz";
import type { AstroliftAgentInteraction } from "@/graphql/agents/agents.types";
import { formatRelativeAge } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { AgentInteractionMapState } from "./use-agent-interaction-map";

export type AgentInteractionMapProps = AgentInteractionMapState;

// An interaction is "live" (pulses its node + incoming edge) while the run is
// non-terminal and the interaction happened within this window of now. Roughly
// three poll cycles, so a burst of activity glows then settles.
const RECENCY_MS = 15000;

// Interaction.status spellings that mean the interaction failed.
const ERROR_STATUSES = new Set(["error", "errored", "failed", "failure", "timed_out"]);

type IconType = React.ComponentType<{ className?: string }>;

// The four interaction kinds, in a fixed left-to-right order so the map's shape
// is stable and always complete. Each renders as a live hub when the run has
// interactions of that kind and stays gracefully empty ("No calls yet")
// otherwise — control_api / tool_call, gate, and signal are all captured now.
const KINDS: { key: string; label: string; icon: IconType }[] = [
  { key: "control_api", label: "Control API", icon: RadioTowerIcon },
  { key: "tool_call", label: "Tools", icon: WrenchIcon },
  { key: "gate", label: "Gates", icon: ShieldIcon },
  { key: "signal", label: "Signals", icon: RadioIcon },
];

// ─── node model ─────────────────────────────────────────────────────────────

type Tone = "success" | "running" | "pending" | "danger" | "muted";

interface InteractionNodeData extends Record<string, unknown> {
  id: string;
  icon: IconType;
  label: string;
  sublabel?: string;
  tone: Tone;
  /** Live node — the status dot + incoming edge pulse. */
  pulse?: boolean;
  /** Upstream node ids (→ incoming edges). */
  needs: string[];
}

// Status tokens (#A1) — the same palette PipelineDag / the fleet map use, so the
// interaction map reads identically to the workflow-run DAG. Token classes only
// (no raw values), so the no-raw-design-values guardrail stays green.
const TONE_RING: Record<Tone, string> = {
  success: "ring-success bg-success/5",
  running: "ring-info bg-info/5",
  pending: "ring-warning bg-warning/5",
  danger: "ring-danger-border bg-danger/5",
  muted: "ring-muted-foreground/20 bg-muted/40",
};

const TONE_DOT: Record<Tone, string> = {
  success: "bg-success",
  running: "bg-info",
  pending: "bg-warning",
  danger: "bg-danger",
  muted: "bg-muted-foreground/40",
};

function InteractionNode({ data }: NodeProps<Node<InteractionNodeData>>) {
  const Icon = data.icon;
  return (
    <div
      className={cn(
        "viz-node bg-background max-w-[220px] min-w-[168px] rounded-md border p-3 shadow-sm ring-1",
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
                "size-1.5 shrink-0 rounded-full",
                TONE_DOT[data.tone],
                // The telemetry live-pulse keys off `.viz-node-dot`.
                "viz-node-dot",
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

const NODE_TYPES: NodeTypes = { interaction: InteractionNode };

// ─── graph builder ────────────────────────────────────────────────────────────

const TASK_ID = "task";

function prettyStatus(status: string): string {
  const s = status.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function isError(status: string): boolean {
  return ERROR_STATUSES.has(status.toLowerCase());
}

// Latest occurredAt in a set of interactions, as epoch ms (NaN if none parse).
function latestMs(items: AstroliftAgentInteraction[]): number {
  let max = NaN;
  for (const it of items) {
    const ms = Date.parse(it.occurredAt);
    if (!Number.isNaN(ms) && (Number.isNaN(max) || ms > max)) max = ms;
  }
  return max;
}

// AgentTask.status → the origin (agent) node's tone + whether it pulses.
function taskTone(status: string): { tone: Tone; pulse: boolean } {
  switch (status.toLowerCase()) {
    case "running":
      return { tone: "running", pulse: true };
    case "provisioning":
    case "queued":
    case "pending":
      return { tone: "pending", pulse: false };
    case "completed":
    case "succeeded":
      return { tone: "success", pulse: false };
    case "failed":
    case "timed_out":
      return { tone: "danger", pulse: false };
    default:
      return { tone: "muted", pulse: false };
  }
}

/**
 * Fold a task's interactions into a layered graph:
 *
 *   agent → kind hub (Control API / Tools / Gates / Signals) → per-name leaf
 *
 * Each layer `needs` the previous, so `rankLayout` lays it out left-to-right by
 * depth (the same longest-path layout PipelineDag / the fleet map use). The four
 * kind hubs always render so the shape is complete; a kind with no interactions
 * stays gracefully empty ("No calls yet"). A hub/leaf is "live" (pulsing, with
 * an animated incoming edge) while the run is non-terminal and its most-recent
 * interaction is within the recency window.
 */
function buildInteractionGraph(
  interactions: AstroliftAgentInteraction[],
  taskStatus: string,
  isTerminal: boolean
): { nodes: Node<InteractionNodeData>[]; edges: Edge[] } {
  const now = Date.now();
  const recent = (ms: number) => !isTerminal && !Number.isNaN(ms) && now - ms < RECENCY_MS;
  const descriptors: InteractionNodeData[] = [];

  // Origin: the agent run.
  const origin = taskTone(taskStatus);
  descriptors.push({
    id: TASK_ID,
    icon: BotIcon,
    label: "Agent",
    sublabel: prettyStatus(taskStatus),
    tone: origin.tone,
    pulse: origin.pulse,
    needs: [],
  });

  const byKind = new Map<string, AstroliftAgentInteraction[]>();
  for (const it of interactions) {
    const list = byKind.get(it.kind) ?? [];
    list.push(it);
    byKind.set(it.kind, list);
  }

  for (const kind of KINDS) {
    const hubId = `kind:${kind.key}`;
    const items = byKind.get(kind.key) ?? [];

    if (items.length === 0) {
      descriptors.push({
        id: hubId,
        icon: kind.icon,
        label: kind.label,
        sublabel: "No calls yet",
        tone: "muted",
        needs: [TASK_ID],
      });
      continue;
    }

    const hubLast = latestMs(items);
    const hubLive = recent(hubLast);
    const hubError = items.some((it) => isError(it.status));
    const ageLabel = Number.isNaN(hubLast)
      ? ""
      : formatRelativeAge(new Date(hubLast).toISOString());
    descriptors.push({
      id: hubId,
      icon: kind.icon,
      label: kind.label,
      sublabel: `${items.length} call${items.length === 1 ? "" : "s"}${ageLabel ? ` · ${ageLabel}` : ""}`,
      tone: hubError ? "danger" : hubLive ? "running" : "success",
      pulse: hubLive,
      needs: [TASK_ID],
    });

    // Leaves: one per distinct interaction name, in first-seen order.
    const byName = new Map<string, AstroliftAgentInteraction[]>();
    for (const it of items) {
      const key = it.name || "(unnamed)";
      const list = byName.get(key) ?? [];
      list.push(it);
      byName.set(key, list);
    }
    for (const [name, group] of byName) {
      const last = latestMs(group);
      const live = recent(last);
      const errored = group.some((it) => isError(it.status));
      const rel = Number.isNaN(last) ? "" : formatRelativeAge(new Date(last).toISOString());
      descriptors.push({
        id: `leaf:${kind.key}:${name}`,
        icon: kind.icon,
        label: name,
        sublabel: `${group.length}×${rel ? ` · ${rel}` : ""}`,
        tone: errored ? "danger" : live ? "running" : "success",
        pulse: live,
        needs: [hubId],
      });
    }
  }

  const ids = descriptors.map((n) => n.id);
  const idSet = new Set(ids);
  const byId = new Map(descriptors.map((n) => [n.id, n]));
  const rankEdges = descriptors.flatMap((n) =>
    n.needs.filter((dep) => idSet.has(dep)).map((dep) => ({ source: dep, target: n.id }))
  );
  const pos = rankLayout(ids, rankEdges);

  const edges: Edge[] = rankEdges.map((e) => {
    const target = byId.get(e.target);
    return {
      id: `${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      // Live edges flow with the telemetry dash.
      animated: !!target?.pulse,
      style: { strokeWidth: 1.5 },
    };
  });

  const nodes: Node<InteractionNodeData>[] = descriptors.map((n) => ({
    id: n.id,
    type: "interaction",
    position: pos.get(n.id) ?? { x: 0, y: 0 },
    data: n,
  }));

  return { nodes, edges };
}

// ─── public component ─────────────────────────────────────────────────────────

/**
 * Per-agent-task interaction map (#1092 — LiveFlowMap P3). Renders the control-
 * plane-observed interactions of a single AgentTask as a live nodes+edges graph:
 * the agent fans out to a hub per interaction kind, and each kind with activity
 * fans out to a leaf per endpoint / tool / gate / signal. Built on the
 * shared telemetry `FlowGraph` substrate — the same one behind the workflow-run
 * DAG (P1) and the fleet map (P2).
 *
 * The graph is rebuilt each fetch; `FlowGraph` reads its graph once on mount, so
 * a signature `key` remounts it to re-flow when the interaction set changes.
 * Once the task is terminal the map settles to a static final shape (no
 * pulses). Reduced-motion falls back to the static look via globals.css.
 */
export function AgentInteractionMapView({
  taskStatus,
  isTerminal,
  interactions,
  loading,
  error,
}: AgentInteractionMapProps) {
  const { nodes, edges } = React.useMemo(
    () => buildInteractionGraph(interactions, taskStatus, isTerminal),
    [interactions, taskStatus, isTerminal]
  );

  // Remount signature: any new interaction (or a task-status change) re-flows the
  // graph, which recomputes recency so the fresh activity lights up.
  const signature = React.useMemo(
    () =>
      `${taskStatus}|${isTerminal}|${interactions
        .map((i) => `${i.id}:${i.status}`)
        .sort()
        .join("|")}`,
    [interactions, taskStatus, isTerminal]
  );

  if (loading && interactions.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-md border p-12">
        <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
      </div>
    );
  }

  if (error && interactions.length === 0) {
    return (
      <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
        {error}
      </div>
    );
  }

  if (interactions.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed p-12 text-center text-sm">
        <WaypointsIcon className="text-muted-foreground mx-auto mb-2 size-5" />
        <p className="text-foreground mb-1 font-medium">No interactions recorded yet</p>
        <p>
          Control API calls, tool invocations, gates, and signals appear here as the run makes them.
        </p>
      </div>
    );
  }

  return (
    <FlowGraph
      key={signature}
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      height={360}
      showMiniMap={false}
      variant="telemetry"
      fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
    />
  );
}
