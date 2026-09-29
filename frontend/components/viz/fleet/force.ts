import { mulberry32 } from "../core/semantics";

/**
 * A small deterministic force simulation for the fleet graph: repulsion,
 * link springs, a pull to the centre, collision, and cooling. Hand-rolled
 * (no d3-force) because the fleet graph needs about a tenth of it. Same
 * inputs and seed, same layout, so stories and tests replay exactly.
 *
 * Integration follows d3's velocity Verlet: forces add to velocity scaled by
 * `alpha`, velocity decays, positions move, alpha cools toward zero. The sim
 * is "at rest" once alpha drops under `alphaMin`.
 */

export interface ForceNodeSpec {
  id: string;
  /** Collision radius in world units. */
  radius: number;
  /** Repulsion this node exerts on every other node. */
  charge: number;
  /** Seed new nodes near their parent, so a graph unfolds from the hub. */
  parent?: string;
}

export interface ForceLinkSpec {
  source: string;
  target: string;
  /** Rest length of the spring. */
  length: number;
  /** 0..1 stiffness; defaults to 0.6. */
  strength?: number;
}

export interface ForceNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  charge: number;
  /** Pinned position; null when free. */
  fx: number | null;
  fy: number | null;
}

export interface ForceLink {
  s: number;
  t: number;
  length: number;
  strength: number;
}

export interface ForceSim {
  nodes: ForceNode[];
  index: Map<string, number>;
  links: ForceLink[];
  width: number;
  height: number;
  alpha: number;
  alphaMin: number;
  alphaDecay: number;
  velocityDecay: number;
  gravity: number;
}

export interface CreateForceSimOptions {
  width: number;
  height: number;
  seed?: number;
  /** A previous sim: nodes present in both keep their position and pin. */
  carry?: ForceSim | null;
}

const ALPHA_MIN = 0.001;
/** Cool from 1 to ALPHA_MIN in about 300 ticks, as d3 does. */
const ALPHA_DECAY = 1 - Math.pow(ALPHA_MIN, 1 / 300);

export function createForceSim(
  specs: ForceNodeSpec[],
  linkSpecs: ForceLinkSpec[],
  { width, height, seed = 1, carry = null }: CreateForceSimOptions
): ForceSim {
  const rng = mulberry32(seed);
  const index = new Map<string, number>();
  const nodes: ForceNode[] = [];
  const cx = width / 2;
  const cy = height / 2;
  // Rest lengths by child, so a new node is seeded about where it will settle.
  const lengthTo = new Map<string, number>();
  for (const l of linkSpecs) lengthTo.set(l.target, l.length);
  let carried = 0;

  for (const spec of specs) {
    const angle = rng() * Math.PI * 2;
    const jitter = rng();
    const prev = carry?.nodes[carry.index.get(spec.id) ?? -1];
    let x: number;
    let y: number;
    let fx: number | null = null;
    let fy: number | null = null;
    if (prev) {
      ({ x, y, fx, fy } = prev);
      carried++;
    } else {
      const parent = spec.parent !== undefined ? nodes[index.get(spec.parent) ?? -1] : undefined;
      const px = parent ? parent.x : cx;
      const py = parent ? parent.y : cy;
      const dist = parent ? (lengthTo.get(spec.id) ?? 40) * (0.6 + jitter * 0.4) : jitter * 20;
      x = px + Math.cos(angle) * dist;
      y = py + Math.sin(angle) * dist;
    }
    index.set(spec.id, nodes.length);
    nodes.push({
      id: spec.id,
      x,
      y,
      vx: 0,
      vy: 0,
      radius: spec.radius,
      charge: spec.charge,
      fx,
      fy,
    });
  }

  const links: ForceLink[] = [];
  for (const l of linkSpecs) {
    const s = index.get(l.source);
    const t = index.get(l.target);
    if (s === undefined || t === undefined) continue;
    links.push({ s, t, length: l.length, strength: l.strength ?? 0.6 });
  }

  const sim: ForceSim = {
    nodes,
    index,
    links,
    width,
    height,
    // Mostly the same graph: settle gently instead of re-exploding it.
    alpha: nodes.length > 0 && carried / nodes.length > 0.5 ? 0.35 : 1,
    alphaMin: ALPHA_MIN,
    alphaDecay: ALPHA_DECAY,
    velocityDecay: 0.4,
    gravity: 0.03,
  };
  clamp(sim);
  return sim;
}

export function isAtRest(sim: ForceSim): boolean {
  return sim.alpha < sim.alphaMin;
}

/** Advance one tick. Returns true while the sim is still moving. */
export function tick(sim: ForceSim): boolean {
  if (isAtRest(sim)) return false;
  const { nodes, links, alpha } = sim;
  const n = nodes.length;

  // Repulsion and collision, all pairs. O(n^2) is fine at a few hundred nodes.
  for (let i = 0; i < n; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = nodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let l2 = dx * dx + dy * dy;
      if (l2 < 1e-6) {
        // Coincident: nudge apart along a direction fixed by the indices.
        dx = ((i * 31 + j * 17) % 7) - 3 || 1;
        dy = ((i * 13 + j * 29) % 5) - 2 || 1;
        l2 = dx * dx + dy * dy;
      }
      const la = (b.charge * alpha) / l2;
      const lb = (a.charge * alpha) / l2;
      a.vx -= dx * la;
      a.vy -= dy * la;
      b.vx += dx * lb;
      b.vy += dy * lb;

      const min = a.radius + b.radius + 2;
      if (l2 < min * min) {
        const l = Math.sqrt(l2);
        const push = ((min - l) / l) * 0.5;
        a.vx -= dx * push;
        a.vy -= dy * push;
        b.vx += dx * push;
        b.vy += dy * push;
      }
    }
  }

  // Link springs, split evenly between the two ends.
  for (const link of links) {
    const a = nodes[link.s];
    const b = nodes[link.t];
    const dx = b.x + b.vx - a.x - a.vx;
    const dy = b.y + b.vy - a.y - a.vy;
    const l = Math.sqrt(dx * dx + dy * dy) || 1e-3;
    const k = ((l - link.length) / l) * alpha * link.strength * 0.5;
    a.vx += dx * k;
    a.vy += dy * k;
    b.vx -= dx * k;
    b.vy -= dy * k;
  }

  // Centre pull, decay, integrate.
  const cx = sim.width / 2;
  const cy = sim.height / 2;
  for (const node of nodes) {
    if (node.fx !== null && node.fy !== null) {
      node.x = node.fx;
      node.y = node.fy;
      node.vx = 0;
      node.vy = 0;
      continue;
    }
    node.vx += (cx - node.x) * sim.gravity * alpha;
    node.vy += (cy - node.y) * sim.gravity * alpha;
    node.vx *= 1 - sim.velocityDecay;
    node.vy *= 1 - sim.velocityDecay;
    node.x += node.vx;
    node.y += node.vy;
  }
  clamp(sim);

  sim.alpha += (0 - sim.alpha) * sim.alphaDecay;
  return !isAtRest(sim);
}

/** Run to rest synchronously (reduced motion, tests). Returns ticks run. */
export function runToRest(sim: ForceSim, maxTicks = 1000): number {
  let ticks = 0;
  while (ticks < maxTicks && tick(sim)) ticks++;
  return ticks;
}

/** Warm the sim back up after a drag or release so neighbours adjust. */
export function reheat(sim: ForceSim, alpha = 0.3): void {
  sim.alpha = Math.max(sim.alpha, alpha);
}

export function pin(sim: ForceSim, id: string, x: number, y: number): void {
  const node = sim.nodes[sim.index.get(id) ?? -1];
  if (!node) return;
  node.fx = Math.max(node.radius, Math.min(sim.width - node.radius, x));
  node.fy = Math.max(node.radius, Math.min(sim.height - node.radius, y));
  node.x = node.fx;
  node.y = node.fy;
  node.vx = 0;
  node.vy = 0;
}

export function release(sim: ForceSim, id: string): void {
  const node = sim.nodes[sim.index.get(id) ?? -1];
  if (!node) return;
  node.fx = null;
  node.fy = null;
}

function clamp(sim: ForceSim) {
  for (const node of sim.nodes) {
    const r = node.radius;
    node.x = Math.max(r, Math.min(sim.width - r, node.x));
    node.y = Math.max(r, Math.min(sim.height - r, node.y));
  }
}

/** The fleet as the graph sees it: which agents hang off which cluster. */
export interface FleetShape {
  clusters: { id: string; agentIds: string[] }[];
  /** Agents whose cluster is not in the snapshot; they hang off the hub. */
  orphans: string[];
}

export const HUB_ID = "hub";
export const clusterNodeId = (id: string) => `c:${id}`;
export const agentNodeId = (id: string) => `a:${id}`;

export const NODE_RADIUS = { hub: 16, cluster: 11, agent: 9 } as const;

export interface FleetForceGraph {
  nodes: ForceNodeSpec[];
  links: ForceLinkSpec[];
  width: number;
  height: number;
}

/**
 * Nodes and springs for a fleet: hub -> cluster -> agent. The world grows
 * with the fleet so a 200-agent graph keeps its spacing; the SVG viewBox
 * scales it back down to the container.
 */
export function fleetForceGraph(shape: FleetShape): FleetForceGraph {
  const agentCount =
    shape.orphans.length + shape.clusters.reduce((n, c) => n + c.agentIds.length, 0);
  const scale = Math.max(1, Math.sqrt(agentCount / 45));
  const nodes: ForceNodeSpec[] = [{ id: HUB_ID, radius: NODE_RADIUS.hub, charge: 900 }];
  const links: ForceLinkSpec[] = [];
  const agent = (id: string, parent: string, length: number) => {
    nodes.push({ id: agentNodeId(id), radius: NODE_RADIUS.agent, charge: 60, parent });
    links.push({ source: parent, target: agentNodeId(id), length, strength: 0.8 });
  };
  for (const c of shape.clusters) {
    const id = clusterNodeId(c.id);
    // Bigger clusters sit further out so their agents have room.
    const spoke = 34 + Math.sqrt(c.agentIds.length) * 7;
    nodes.push({ id, radius: NODE_RADIUS.cluster, charge: 500, parent: HUB_ID });
    links.push({ source: HUB_ID, target: id, length: 90 + spoke * 1.5, strength: 0.5 });
    for (const a of c.agentIds) agent(a, id, spoke);
  }
  for (const a of shape.orphans) agent(a, HUB_ID, 70);
  return { nodes, links, width: Math.round(900 * scale), height: Math.round(600 * scale) };
}
