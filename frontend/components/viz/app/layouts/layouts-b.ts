import type { AppEdge, AppEvent, AppNode, AppNodeRole, AppSnapshot } from "../../core/app-model";

/**
 * Pure derivations for the service-agent, agent, functions and jobs auto
 * layouts (spec 44 viz addendum). Everything here reads the AppSnapshot and
 * nothing else, so the pictures only show state the model carries.
 */

/** stepApp keeps events for 12s; recency fades over the same window. */
export const EVENT_WINDOW_MS = 12_000;
/** A call or invocation this recent still ripples its target. */
export const FLASH_MS = 1_500;
/** An invocation after this much quiet reads as a cold start. */
export const COLD_GAP_MS = 5_000;
/** Over this share of failed calls an edge reads as failing (the model's rule). */
export const EDGE_FAIL_RATE = 0.05;
/** Load at or over this is "busy" in summaries. */
export const BUSY_LOAD = 0.7;

export function edgeFailing(e: AppEdge): boolean {
  return e.errorRate > EDGE_FAIL_RATE;
}

/** Stable 0..1 from an id, so a derived choice replays identically. */
export function unitHash(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

export function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, Math.max(1, max - 1))}…` : s;
}

/** Recency as a still opacity: 1 when new, fading to a floor at the window edge. */
export function recency(age: number, floor = 0.15): number {
  return Math.max(floor, 1 - Math.max(0, age) / EVENT_WINDOW_MS);
}

// ---------------------------------------------------------------- agent feed

export interface FeedItem {
  id: string;
  at: number;
  age: number;
  agentId: string;
  target?: AppNode;
  edge?: AppEdge;
  verb: string;
  failing: boolean;
}

const VERB: Partial<Record<AppNodeRole, string>> = {
  data: "queried",
  queue: "published to",
  function: "invoked",
  external: "called",
  service: "called",
};

/**
 * What the agent is doing: each agent_action event, newest first, with the
 * call it made. The event names the agent; the target is its `detail` when
 * that names a node, else one of the agent's outgoing edges chosen by the
 * event id in proportion to that edge's traffic.
 */
export function agentFeed(snapshot: AppSnapshot, agentId?: string): FeedItem[] {
  const byId = new Map(snapshot.nodes.map((n) => [n.id, n]));
  return snapshot.events
    .filter((e) => e.kind === "agent_action" && (!agentId || e.nodeId === agentId))
    .map((e) => {
      const out = snapshot.edges.filter((x) => x.from === e.nodeId);
      const named = e.detail
        ? snapshot.nodes.find((n) => n.id === e.detail || n.name === e.detail)
        : undefined;
      let edge = named ? out.find((x) => x.to === named.id) : undefined;
      if (!named && out.length) {
        const total = out.reduce((s, x) => s + Math.max(1, x.rps), 0);
        let pick = unitHash(e.id) * total;
        edge = out.find((x) => (pick -= Math.max(1, x.rps)) < 0) ?? out[out.length - 1];
      }
      const target = named ?? (edge ? byId.get(edge.to) : undefined);
      return {
        id: e.id,
        at: e.at,
        age: snapshot.now - e.at,
        agentId: e.nodeId,
        target,
        edge,
        verb: (target && VERB[target.role]) ?? "worked on",
        failing: (edge ? edgeFailing(edge) : false) || target?.health === "failing",
      };
    })
    .sort((a, b) => b.at - a.at);
}

/** The newest feed item per target node. */
export function lastCallByTarget(feed: FeedItem[]): Map<string, FeedItem> {
  const m = new Map<string, FeedItem>();
  for (const f of feed) if (f.target && !m.has(f.target.id)) m.set(f.target.id, f);
  return m;
}

// ----------------------------------------------------------------- layering

/**
 * Longest-path columns from the sources, so triggers sit left and sinks
 * right. Cycles are cut by capping depth at the node count.
 */
export function layerNodes(nodes: AppNode[], edges: AppEdge[]): AppNode[][] {
  const depth = new Map(nodes.map((n) => [n.id, 0]));
  const ids = new Set(nodes.map((n) => n.id));
  const live = edges.filter((e) => ids.has(e.from) && ids.has(e.to) && e.from !== e.to);
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of live) {
      const d = (depth.get(e.from) ?? 0) + 1;
      if (d > (depth.get(e.to) ?? 0) && d < nodes.length) {
        depth.set(e.to, d);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const cols: AppNode[][] = [];
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    (cols[d] ??= []).push(n);
  }
  return cols.filter(Boolean);
}

// ---------------------------------------------------------------- functions

export interface FunctionStat {
  node: AppNode;
  /** Invocations seen in the event window. */
  invocations: number;
  last?: AppEvent;
  /** The latest invocation came after COLD_GAP_MS of quiet. */
  cold: boolean;
}

export function functionStats(snapshot: AppSnapshot): FunctionStat[] {
  const windowStart = snapshot.now - EVENT_WINDOW_MS;
  return snapshot.nodes
    .filter((n) => n.role === "function")
    .map((node) => {
      const inv = snapshot.events
        .filter((e) => e.kind === "invoked" && e.nodeId === node.id)
        .sort((a, b) => a.at - b.at);
      const last = inv[inv.length - 1];
      const prevAt = inv.length > 1 ? inv[inv.length - 2].at : windowStart;
      const cold = !!last && last.at - prevAt >= COLD_GAP_MS;
      return { node, invocations: inv.length, last, cold };
    });
}

// ----------------------------------------------------------------- schedule

type Field = (v: number) => boolean;

function field(src: string, max: number): Field | null {
  if (src === "*") return () => true;
  const step = /^\*\/(\d+)$/.exec(src);
  if (step) {
    const n = Number(step[1]);
    return n > 0 ? (v) => v % n === 0 : null;
  }
  const list = src.split(",").map(Number);
  if (list.some((v) => !Number.isInteger(v) || v < 0 || v > max)) return null;
  return (v) => list.includes(v);
}

export interface Schedule {
  /** Whether a UTC minute (epoch ms at a minute boundary) is a run slot. */
  matches: (ms: number) => boolean;
  label: string;
}

/**
 * Parse the schedule node's name: a five-field cron with minute and hour
 * fields (day fields must be `*`), or "every N min" / "every N h".
 * Times are UTC. Returns null for anything else.
 */
export function parseSchedule(src: string): Schedule | null {
  const every = /^every\s+(\d+)\s*(m|min|mins|minutes?|h|hr|hours?)$/i.exec(src.trim());
  if (every) {
    const n = Number(every[1]);
    const mins = /^h/i.test(every[2]) ? n * 60 : n;
    if (mins <= 0) return null;
    return { matches: (ms) => Math.floor(ms / 60_000) % mins === 0, label: src.trim() };
  }
  const parts = src.trim().split(/\s+/);
  if (parts.length !== 5 || parts.slice(2).some((p) => p !== "*")) return null;
  const minute = field(parts[0], 59);
  const hour = field(parts[1], 23);
  if (!minute || !hour) return null;
  return {
    matches: (ms) => {
      const d = new Date(ms);
      return minute(d.getUTCMinutes()) && hour(d.getUTCHours());
    },
    label: `${src.trim()} UTC`,
  };
}

const MINUTE = 60_000;
const SEARCH_LIMIT = 8 * 24 * 60;

export function nextRun(schedule: Schedule, now: number): number | null {
  let t = Math.floor(now / MINUTE) * MINUTE + MINUTE;
  for (let i = 0; i < SEARCH_LIMIT; i++, t += MINUTE) if (schedule.matches(t)) return t;
  return null;
}

/** The last `count` slots at or before now, oldest first. */
export function pastRuns(schedule: Schedule, now: number, count: number): number[] {
  const out: number[] = [];
  let t = Math.floor(now / MINUTE) * MINUTE;
  for (let i = 0; i < SEARCH_LIMIT * 2 && out.length < count; i++, t -= MINUTE) {
    if (schedule.matches(t)) out.push(t);
  }
  return out.reverse();
}

export function formatCountdown(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

// ------------------------------------------------------------------- mixed

export const ROLE_SECTIONS: { title: string; roles: AppNodeRole[] }[] = [
  { title: "Traffic", roles: ["ingress"] },
  { title: "Services", roles: ["service"] },
  { title: "Functions", roles: ["trigger", "function"] },
  { title: "Jobs", roles: ["schedule", "worker"] },
  { title: "Agents", roles: ["agent"] },
  { title: "Queues", roles: ["queue"] },
  { title: "Data", roles: ["data"] },
  { title: "External", roles: ["external"] },
];

export function roleSections(nodes: AppNode[]): { title: string; nodes: AppNode[] }[] {
  return ROLE_SECTIONS.map((s) => ({
    title: s.title,
    nodes: nodes.filter((n) => s.roles.includes(n.role)),
  })).filter((s) => s.nodes.length > 0);
}

// ---------------------------------------------------------------- summaries

export function summarize(nodes: AppNode[], noun: string): string {
  const failing = nodes.filter((n) => n.health === "failing").length;
  const busy = nodes.filter((n) => n.health !== "failing" && n.load >= BUSY_LOAD).length;
  const idle = nodes.filter((n) => n.health === "idle").length;
  const parts = [`${nodes.length} ${noun}`];
  if (failing) parts.push(`${failing} failing`);
  if (busy) parts.push(`${busy} busy`);
  if (idle) parts.push(`${idle} idle`);
  return parts.join(", ");
}
