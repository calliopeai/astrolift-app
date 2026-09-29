import type { AppEdge, AppEvent, AppNode, AppNodeRole, AppSnapshot } from "../core/app-model";
import { iso, type Point } from "../core/iso";
import type { Health } from "../core/semantics";

/**
 * Where things sit in the two generic app renderers (AppIsometric, AppGraph),
 * kept pure so it is tested apart from the SVG. Both read the same rules:
 * a layer per role, an edge's health from its error rate, its speed from its
 * request rate, and which events are recent enough to show.
 */

/** Over this share of failed calls an edge reads as failing. */
export const ERROR_EDGE = 0.05;
/** Load at or over this counts a node as busy in the summary. */
export const BUSY_LOAD = 0.7;
/** How long an invoke or agent action lights its node, ms. */
export const FLASH_MS = 2500;
/** Events older than this are not drawn (the model keeps 12 s). */
export const EVENT_WINDOW_MS = 12_000;
/** Request rate that reads as full speed; the scale is logarithmic. */
export const RPS_FULL = 500;

/** Left to right: what calls in, what runs, what it keeps or calls out to. */
export const LAYER: Record<AppNodeRole, 0 | 1 | 2> = {
  ingress: 0,
  trigger: 0,
  schedule: 0,
  service: 1,
  worker: 1,
  function: 1,
  agent: 1,
  data: 2,
  queue: 2,
  external: 2,
};

export const ROLE_TAG: Record<AppNodeRole, string> = {
  ingress: "ingress",
  trigger: "trigger",
  schedule: "schedule",
  service: "service",
  worker: "worker",
  function: "function",
  agent: "agent",
  data: "data",
  queue: "queue",
  external: "external",
};

export function nodeLabel(node: AppNode, h: Health, invokes: number, actions: number): string {
  const parts = [`${node.name} (${node.role})`, h, `load ${Math.round(node.load * 100)}%`];
  if (node.replicas) parts.push(`${node.replicas.ready} of ${node.replicas.desired} replicas`);
  if (node.rps !== undefined) parts.push(`${Math.round(node.rps)} per second`);
  if (invokes) parts.push(`${invokes} invocations in 12 s`);
  if (actions) parts.push(`${actions} actions in 12 s`);
  return parts.join(", ");
}

export function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, Math.max(1, n - 1))}…` : s;
}

/** An edge's health: failing over the error threshold, idle with no traffic. */
export function edgeHealth(e: AppEdge): Health {
  if (e.errorRate > ERROR_EDGE) return "failing";
  if (e.rps <= 0) return "idle";
  return "ok";
}

/** 0..1 throughput on a log scale, so 10 rps and 400 rps both read. */
export function edgeRate(rps: number): number {
  if (rps <= 0) return 0;
  return Math.min(1, Math.log10(1 + rps) / Math.log10(1 + RPS_FULL));
}

/** Particles on an edge: none when idle, up to four when saturated. */
export function particleCount(rate: number): number {
  return rate <= 0 ? 0 : 1 + Math.round(rate * 3);
}

/** A node's events of one kind still in the window, newest last. */
export function recentEvents(
  snapshot: AppSnapshot,
  nodeId: string,
  kind: AppEvent["kind"]
): AppEvent[] {
  return snapshot.events
    .filter((e) => e.nodeId === nodeId && e.kind === kind && snapshot.now - e.at < EVENT_WINDOW_MS)
    .sort((a, b) => a.at - b.at);
}

/** The newest event of a kind if it is inside the flash window. */
export function flashEvent(
  snapshot: AppSnapshot,
  nodeId: string,
  kind: AppEvent["kind"]
): AppEvent | undefined {
  const last = recentEvents(snapshot, nodeId, kind).at(-1);
  return last && snapshot.now - last.at < FLASH_MS ? last : undefined;
}

/** 1 for an event just now, falling to 0 at the end of the window. */
export function recency(snapshot: AppSnapshot, e: AppEvent | undefined): number {
  if (!e) return 0;
  return Math.max(0, 1 - (snapshot.now - e.at) / EVENT_WINDOW_MS);
}

/** "9 workloads, 1 failing, 3 busy, 2 edges erroring" for the aria-label. */
export function describeApp(snapshot: AppSnapshot): string {
  const count = (h: Health) => snapshot.nodes.filter((n) => n.health === h).length;
  const busy = snapshot.nodes.filter((n) => n.health !== "failing" && n.load >= BUSY_LOAD).length;
  const erroring = snapshot.edges.filter((e) => e.errorRate > ERROR_EDGE).length;
  const parts = [`${snapshot.app.name}: ${snapshot.nodes.length} nodes`];
  if (count("failing")) parts.push(`${count("failing")} failing`);
  if (count("degraded")) parts.push(`${count("degraded")} degraded`);
  if (busy) parts.push(`${busy} busy`);
  if (count("idle")) parts.push(`${count("idle")} idle`);
  parts.push(
    erroring
      ? `${erroring} of ${snapshot.edges.length} connections erroring`
      : `${snapshot.edges.length} connections healthy`
  );
  return parts.join(", ");
}

/**
 * Nodes grouped into the layers that are present (empty layers collapse), and
 * ordered within a layer by the mean position of their neighbours in the
 * layer before, which keeps most edges from crossing.
 */
export function orderLayers(snapshot: AppSnapshot): AppNode[][] {
  const byLayer: AppNode[][] = [[], [], []];
  snapshot.nodes.forEach((n) => byLayer[LAYER[n.role]].push(n));
  const layers = byLayer.filter((l) => l.length > 0);
  const original = new Map(snapshot.nodes.map((n, i) => [n.id, i]));
  for (let li = 1; li < layers.length; li++) {
    const prev = new Map(layers[li - 1].map((n, i) => [n.id, i]));
    const key = (n: AppNode) => {
      const ns = snapshot.edges
        .map((e) => (e.to === n.id ? e.from : e.from === n.id ? e.to : null))
        .filter((id): id is string => id !== null && prev.has(id))
        .map((id) => prev.get(id)!);
      // Unconnected to the previous layer: after the connected ones, in model order.
      return ns.length
        ? ns.reduce((a, b) => a + b, 0) / ns.length
        : 1000 + (original.get(n.id) ?? 0);
    };
    layers[li] = [...layers[li]].sort(
      (a, b) => key(a) - key(b) || original.get(a.id)! - original.get(b.id)!
    );
  }
  return layers;
}

export function polylineLength(pts: Point[]): number {
  let len = 0;
  for (let i = 1; i < pts.length; i++) {
    len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  }
  return len;
}

/** The point a fraction `f` (0..1) of the way along a polyline. */
export function pointAlong(pts: Point[], f: number): Point {
  if (pts.length === 1) return pts[0];
  const total = polylineLength(pts);
  let left = Math.max(0, Math.min(1, f)) * total;
  for (let i = 1; i < pts.length; i++) {
    const [ax, ay] = pts[i - 1];
    const [bx, by] = pts[i];
    const seg = Math.hypot(bx - ax, by - ay);
    if (left <= seg || i === pts.length - 1) {
      const t = seg === 0 ? 0 : Math.min(1, left / seg);
      return [ax + (bx - ax) * t, ay + (by - ay) * t];
    }
    left -= seg;
  }
  return pts[pts.length - 1];
}

/** A cubic Bezier sampled into a polyline, for particles to ride. */
export function cubicPoints(p0: Point, p1: Point, p2: Point, p3: Point, samples = 16): Point[] {
  return Array.from({ length: samples + 1 }, (_, i) => {
    const t = i / samples;
    const u = 1 - t;
    const a = u * u * u;
    const b = 3 * u * u * t;
    const c = 3 * u * t * t;
    const d = t * t * t;
    return [
      a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
      a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    ] as Point;
  });
}

export const encodePts = (pts: Point[]) =>
  pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

export function decodePts(s: string): Point[] {
  return s.split(" ").map((p) => p.split(",").map(Number) as Point);
}

/* ---------------------------------------------------------------- graph */

export const G_NODE_W = 144;
export const G_NODE_H = 46;
export const G_COL_GAP = 236;
export const G_ROW_GAP = 66;
export const G_PAD = 20;

export interface GraphNodeBox {
  node: AppNode;
  x: number;
  y: number;
  col: number;
}

export interface GraphEdgePath {
  edge: AppEdge;
  id: string;
  d: string;
  pts: Point[];
  mid: Point;
  health: Health;
  rate: number;
}

export interface AppGraphLayout {
  width: number;
  height: number;
  nodes: GraphNodeBox[];
  edges: GraphEdgePath[];
}

export function layoutAppGraph(snapshot: AppSnapshot): AppGraphLayout {
  const layers = orderLayers(snapshot);
  const rows = Math.max(1, ...layers.map((l) => l.length));
  const height = G_PAD * 2 + rows * G_ROW_GAP - (G_ROW_GAP - G_NODE_H) + 14;
  const cols = Math.max(1, layers.length);
  const width = G_PAD * 2 + G_NODE_W + (cols - 1) * G_COL_GAP;
  const nodes: GraphNodeBox[] = [];
  layers.forEach((layer, col) => {
    const offset = ((rows - layer.length) * G_ROW_GAP) / 2;
    layer.forEach((node, row) => {
      nodes.push({ node, col, x: G_PAD + col * G_COL_GAP, y: G_PAD + offset + row * G_ROW_GAP });
    });
  });
  const at = new Map(nodes.map((b) => [b.node.id, b]));
  const edges: GraphEdgePath[] = [];
  snapshot.edges.forEach((edge, i) => {
    const s = at.get(edge.from);
    const t = at.get(edge.to);
    if (!s || !t) return;
    const sy = s.y + G_NODE_H / 2;
    const ty = t.y + G_NODE_H / 2;
    let p0: Point, p1: Point, p2: Point, p3: Point;
    if (t.col > s.col) {
      p0 = [s.x + G_NODE_W, sy];
      p3 = [t.x, ty];
      const k = (p3[0] - p0[0]) / 2;
      p1 = [p0[0] + k, sy];
      p2 = [p3[0] - k, ty];
    } else if (t.col < s.col) {
      // A call back toward the left (a queue feeding a service): leave from
      // the left side and arrive on the right, so the curve does not cross nodes.
      p0 = [s.x, sy];
      p3 = [t.x + G_NODE_W, ty];
      const k = (p0[0] - p3[0]) / 2;
      p1 = [p0[0] - k, sy];
      p2 = [p3[0] + k, ty];
    } else {
      p0 = [s.x + G_NODE_W, sy];
      p3 = [t.x + G_NODE_W, ty];
      p1 = [p0[0] + 40, sy];
      p2 = [p3[0] + 40, ty];
    }
    const pts = cubicPoints(p0, p1, p2, p3);
    edges.push({
      edge,
      id: `${edge.from}->${edge.to}#${i}`,
      d: `M${p0[0]} ${p0[1]} C${p1[0]} ${p1[1]} ${p2[0]} ${p2[1]} ${p3[0]} ${p3[1]}`,
      pts,
      mid: pts[Math.floor(pts.length / 2)],
      health: edgeHealth(edge),
      rate: edgeRate(edge.rps),
    });
  });
  // The graph may need room on the right for same-column loops.
  return { width: width + 44, height, nodes, edges };
}

/* ------------------------------------------------------------ isometric */

/** Pixels per world unit. */
export const ISO_UNIT = 30;
/** World distance between columns (along x) and rows (along y). */
export const ISO_COL = 3.6;
export const ISO_ROW = 2.8;
/** Most blocks in one column before it folds, and the gap between folds. */
export const ISO_MAX_ROWS = 4;
export const ISO_SUB = 2.4;
/** The tallest a block can get (a saturated service with a replica stack). */
export const ISO_MAX_Z = 4;
/** Replica slabs drawn before the rest become a count. */
export const MAX_SLABS = 5;

/** Footprint (w along x, d along y) per role, world units. */
export const FOOTPRINT: Record<AppNodeRole, [number, number]> = {
  ingress: [1.4, 1.4],
  trigger: [1.4, 1.4],
  schedule: [1.4, 1.4],
  external: [1.4, 1.4],
  service: [1.4, 1.4],
  worker: [1.4, 1.4],
  agent: [1.2, 1.2],
  function: [0.7, 0.7],
  data: [1.3, 1.3],
  queue: [1, 2.2],
};

/** Services and workers: base height grows with load. */
export function serviceHeight(load: number): number {
  return 0.4 + Math.max(0, Math.min(1, load)) * 2.2;
}

/** Data: a cylinder that fills with connections in use. */
export function dataHeight(load: number): number {
  return 0.5 + Math.max(0, Math.min(1, load)) * 1.3;
}

export interface IsoNodePlace {
  node: AppNode;
  /** Footprint origin (the far corner), world units. */
  wx: number;
  wy: number;
  w: number;
  d: number;
  /** Centre on the floor, world units. */
  cx: number;
  cy: number;
}

export interface IsoEdgePath {
  edge: AppEdge;
  id: string;
  pts: Point[];
  mid: Point;
  health: Health;
  rate: number;
}

export interface AppIsoLayout {
  viewBox: [number, number, number, number];
  platform: { x0: number; y0: number; x1: number; y1: number };
  nodes: IsoNodePlace[];
  edges: IsoEdgePath[];
}

/**
 * Columns follow the graph's layers, except the outside world: ingress,
 * triggers and schedules dock on the near rim, externals on the far rim.
 */
export function isoColumn(role: AppNodeRole, present: Set<number>): number {
  const base = role === "external" ? 3 : LAYER[role];
  return [...present].filter((c) => c < base).length;
}

export function layoutAppIso(snapshot: AppSnapshot): AppIsoLayout {
  const colOf = (n: AppNode) => (n.role === "external" ? 3 : LAYER[n.role]);
  const present = new Set(snapshot.nodes.map(colOf));
  const groups = new Map<number, AppNode[]>();
  // Reuse the graph ordering so both views put a node in the same place.
  orderLayers(snapshot)
    .flat()
    .forEach((n) => {
      const c = isoColumn(n.role, present);
      groups.set(c, [...(groups.get(c) ?? []), n]);
    });
  // A tall column folds into side-by-side sub-columns so a big app stays
  // roughly square instead of a long thin strip.
  const folded = [...groups.entries()]
    .sort(([a], [b]) => a - b)
    .map(([, group]) => {
      const subs = Math.ceil(group.length / ISO_MAX_ROWS);
      return { group, subs, perSub: Math.ceil(group.length / subs) };
    });
  const rows = Math.max(1, ...folded.map((f) => f.perSub));
  const nodes: IsoNodePlace[] = [];
  let colX = 0;
  folded.forEach(({ group, subs, perSub }, c) => {
    if (c > 0) colX += ISO_COL;
    group.forEach((node, r) => {
      const sub = Math.floor(r / perSub);
      const inSub = Math.min(perSub, group.length - sub * perSub);
      const [w, d] = FOOTPRINT[node.role];
      const cx = colX + sub * ISO_SUB;
      const cy = ((rows - inSub) * ISO_ROW) / 2 + (r % perSub) * ISO_ROW;
      nodes.push({ node, w, d, cx, cy, wx: cx - w / 2, wy: cy - d / 2 });
    });
    colX += (subs - 1) * ISO_SUB;
  });
  const platform = {
    x0: -1.3,
    y0: -1.5,
    x1: colX + 1.3,
    y1: (rows - 1) * ISO_ROW + 1.5,
  };

  const at = new Map(nodes.map((p) => [p.node.id, p]));
  const edges: IsoEdgePath[] = [];
  snapshot.edges.forEach((edge, i) => {
    const s = at.get(edge.from);
    const t = at.get(edge.to);
    if (!s || !t) return;
    // A floor path: out along x to the gap between columns, across along y, in.
    const mx = s.cx === t.cx ? s.cx + ISO_COL / 2 : (s.cx + t.cx) / 2;
    const world: [number, number][] = [
      [s.cx, s.cy],
      [mx, s.cy],
      [mx, t.cy],
      [t.cx, t.cy],
    ];
    const pts = world.map(([x, y]) => iso(x, y, 0, ISO_UNIT));
    edges.push({
      edge,
      id: `${edge.from}->${edge.to}#${i}`,
      pts,
      mid: pointAlong(pts, 0.5),
      health: edgeHealth(edge),
      rate: edgeRate(edge.rps),
    });
  });

  // A stable frame: bounds of the platform plus the tallest a block may get,
  // so the picture does not jump as load changes.
  const corners = [
    iso(platform.x0, platform.y0, ISO_MAX_Z, ISO_UNIT),
    iso(platform.x1, platform.y0, ISO_MAX_Z, ISO_UNIT),
    iso(platform.x0, platform.y1, ISO_MAX_Z, ISO_UNIT),
    iso(platform.x1, platform.y1, -0.4, ISO_UNIT),
    iso(platform.x0, platform.y1, -0.4, ISO_UNIT),
    iso(platform.x1, platform.y0, -0.4, ISO_UNIT),
  ];
  const xs = corners.map((p) => p[0]);
  const ys = corners.map((p) => p[1]);
  const pad = 24;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - pad;
  return {
    viewBox: [minX, minY, Math.max(...xs) + pad - minX, Math.max(...ys) + pad + 14 - minY],
    platform,
    nodes,
    edges,
  };
}
