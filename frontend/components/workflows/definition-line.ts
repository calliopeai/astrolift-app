import { readBackEdge } from "./back-edge";
import type {
  WorkflowLine,
  WorkflowLoop,
  WorkflowSnapshot,
  WorkflowStation,
} from "@/components/viz/core/workflow-model";

/**
 * A workflow definition as the one line every workflow view draws (the viz
 * model in components/viz/core/workflow-model.ts), so the Builder tab and the
 * list's card glyph show the shape the backend runs: ordered stages, fan-out
 * merged by an aggregation, human gates, nested child workflows, and retries.
 *
 * Return tracks and retry ceilings come from the authored stage contract.
 */

/** What the line needs of a stage; the list's topology stage and the builder's stage both have it. */
export interface LineStage {
  guid: string;
  order: number;
  kind: string;
  role: string;
  workflowRef: string;
  fanOutCount: number | null;
  /** The branch count is decided per run. Topology stages only. */
  fanOutDynamic?: boolean;
  onFailure: string;
  maxAttempts?: number;
  backEdge?: unknown;
  outputKey?: string;
  /** The agent the stage dispatches, when its role is blank. */
  agentName?: string | null;
}

/**
 * A retrying stage runs at most this many times: MAX_STAGE_ATTEMPTS in
 * backend astrolift_workflows/workflows/workflow_definition_run.py.
 */
export const STAGE_MAX_ATTEMPTS = 3;

const KIND_NAME: Record<string, string> = {
  agent_dispatch: "Agent",
  human_gate: "Gate",
  aggregation: "Merge",
  checkpoint: "Checkpoint",
  workflow: "Workflow",
};

function words(value: string): string {
  const s = value.replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
}

function stationName(stage: LineStage): string {
  if (stage.kind === "workflow" && stage.workflowRef) return stage.workflowRef;
  return (
    words(stage.role) || stage.agentName || KIND_NAME[stage.kind] || words(stage.kind) || "Stage"
  );
}

function fanned(stage: LineStage): boolean {
  return (stage.fanOutCount ?? 0) > 1 || Boolean(stage.fanOutDynamic);
}

/** The definition's stages, in order, as one line of stations with its retry loops. */
export function definitionLine(
  definition: { slug: string; name: string; patternKind: string },
  stages: readonly LineStage[]
): WorkflowLine {
  const ordered = [...stages].sort((a, b) => a.order - b.order);
  const stations: WorkflowStation[] = [];
  const loops: WorkflowLoop[] = [];
  let openFanout: number | null = null;

  ordered.forEach((stage, i) => {
    const id = stage.guid;
    const name = stationName(stage);
    const count = (stage.fanOutCount ?? 0) > 1 ? (stage.fanOutCount ?? 0) : 0;
    if (stage.kind === "agent_dispatch" && fanned(stage)) {
      stations.push({
        id,
        name,
        kind: "fanout",
        dynamic: Boolean(stage.fanOutDynamic) || undefined,
        branches: Array.from({ length: count }, (_, b) => ({
          id: `${id}-b${b}`,
          label: `#${b + 1}`,
          state: "queued" as const,
        })),
      });
      openFanout = i;
    } else if (stage.kind === "aggregation" && openFanout !== null) {
      stations.push({ id, name, kind: "join", waitsOn: openFanout });
      openFanout = null;
    } else if (stage.kind === "workflow") {
      stations.push({ id, name, kind: "workflow", childLineId: `definition:${stage.workflowRef}` });
    } else {
      stations.push({ id, name, kind: stage.kind === "human_gate" ? "gate" : "stage" });
    }
    if (stage.onFailure === "retry" && ["agent_dispatch", "workflow"].includes(stage.kind)) {
      loops.push({
        id: `${id}-retry`,
        from: i,
        to: i,
        trigger: stage.kind === "human_gate" ? "rejected" : "failed",
        maxRounds: stage.maxAttempts ?? STAGE_MAX_ATTEMPTS,
        kind: "retry",
      });
    }
  });

  ordered.forEach((stage, from) => {
    const edge = readBackEdge(stage.backEdge);
    if (!edge || !stage.outputKey) return;
    const to = ordered.findIndex((target) => target.outputKey === edge.to);
    if (to < 0 || to >= from) return;
    loops.push({
      id: `${stage.outputKey}->${edge.to}`,
      from,
      to,
      trigger:
        edge.when === "gate_rejected"
          ? "rejected"
          : edge.when === "stage_failed"
            ? "failed"
            : "condition",
      ...(edge.when === "output_equals"
        ? { condition: `${edge.path} = ${JSON.stringify(edge.value)}` }
        : edge.when === "always"
          ? { condition: "always" }
          : {}),
      maxRounds: edge.max_rounds,
      kind: "back-edge",
    });
  });

  return {
    id: `definition:${definition.slug}`,
    name: definition.name,
    stations,
    ...(loops.length > 0 ? { loops } : {}),
  };
}

/** The line as a snapshot with no runs on it: nothing moves, because nothing is running. */
export function definitionSnapshot(line: WorkflowLine, now = 0): WorkflowSnapshot {
  return { now, lines: [line], trains: [], segments: [] };
}

/** The shape in words, for a glyph's accessible name: "4 stages: fan-out, join, gate, 1 retry". */
export function lineShape(line: WorkflowLine): string {
  const n = line.stations.length;
  if (n === 0) return "No stages";
  const parts: string[] = [];
  const count = (kind: WorkflowStation["kind"]) =>
    line.stations.filter((s) => s.kind === kind).length;
  const add = (k: number, one: string, many = `${one}s`) => {
    if (k > 0) parts.push(k === 1 ? one : `${k} ${many}`);
  };
  add(count("fanout"), "fan-out");
  add(count("join"), "join");
  add(count("supervisor"), "supervisor");
  add(count("gate"), "gate");
  add(count("workflow"), "nested workflow");
  const retries = line.loops?.filter((loop) => loop.kind === "retry").length ?? 0;
  const returns = line.loops?.filter((loop) => loop.kind === "back-edge").length ?? 0;
  if (returns > 0) parts.push(`${returns} ${returns === 1 ? "return edge" : "return edges"}`);
  if (retries > 0) parts.push(`${retries} ${retries === 1 ? "retry" : "retries"}`);
  const head = `${n} ${n === 1 ? "stage" : "stages"}`;
  return parts.length > 0 ? `${head}: ${parts.join(", ")}` : head;
}
