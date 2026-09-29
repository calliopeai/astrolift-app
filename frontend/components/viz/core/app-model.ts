import type { TopologyKind } from "@/lib/topology";

import { mulberry32, type Health } from "./semantics";

/**
 * The one data model app dashboards draw, for every topology and every app
 * view style (auto, isometric, graph, classic). Nodes are workloads, managed
 * services and the outside world; edges carry live traffic.
 */

export type AppNodeRole =
  | "ingress"
  | "service"
  | "worker"
  | "data"
  | "queue"
  | "function"
  | "trigger"
  | "agent"
  | "schedule"
  | "external";

export interface AppNode {
  id: string;
  name: string;
  role: AppNodeRole;
  /** Workload kind or managed service kind, for the icon (deployment, postgres, sqs...). */
  kind: string;
  health: Health;
  /** 0..1 utilisation (CPU for services, connections for data, concurrency for functions). */
  load: number;
  replicas?: { ready: number; desired: number };
  /** Requests or invocations per second through this node. */
  rps?: number;
  /** Median latency, ms. */
  p50?: number;
}

export interface AppEdge {
  from: string;
  to: string;
  /** Requests or messages per second. */
  rps: number;
  /** 0..1 share of failed calls. Over 0.05 the edge reads as failing. */
  errorRate: number;
}

export interface AppEvent {
  id: string;
  kind:
    | "deploy_started"
    | "deploy_finished"
    | "scaled"
    | "invoked"
    | "agent_action"
    | "error_spike";
  nodeId: string;
  at: number;
  detail?: string;
}

export interface AppSnapshot {
  now: number;
  app: { slug: string; name: string };
  topology: TopologyKind;
  nodes: AppNode[];
  edges: AppEdge[];
  events: AppEvent[];
}

export interface AppViewProps {
  snapshot: AppSnapshot;
  motion: "full" | "reduced";
  flowParticles?: boolean;
  onSelectNode?: (nodeId: string) => void;
  className?: string;
}

type Spec = [id: string, name: string, role: AppNodeRole, kind: string][];

const SHAPES: Record<TopologyKind, { nodes: Spec; edges: [string, string][] }> = {
  service: {
    nodes: [
      ["in", "Internet", "ingress", "ingress"],
      ["web", "web", "service", "deployment"],
    ],
    edges: [["in", "web"]],
  },
  "service-data": {
    nodes: [
      ["in", "Internet", "ingress", "ingress"],
      ["web", "web", "service", "deployment"],
      ["db", "postgres", "data", "postgres"],
      ["cache", "redis", "data", "redis"],
    ],
    edges: [
      ["in", "web"],
      ["web", "db"],
      ["web", "cache"],
    ],
  },
  "service-worker": {
    nodes: [
      ["in", "Internet", "ingress", "ingress"],
      ["web", "web", "service", "deployment"],
      ["q", "jobs", "queue", "sqs"],
      ["worker", "worker", "worker", "deployment"],
      ["db", "postgres", "data", "postgres"],
    ],
    edges: [
      ["in", "web"],
      ["web", "q"],
      ["q", "worker"],
      ["web", "db"],
      ["worker", "db"],
    ],
  },
  microservices: {
    nodes: [
      ["in", "Internet", "ingress", "ingress"],
      ["gw", "gateway", "service", "deployment"],
      ["users", "users", "service", "deployment"],
      ["orders", "orders", "service", "deployment"],
      ["billing", "billing", "service", "deployment"],
      ["search", "search", "service", "deployment"],
      ["db", "postgres", "data", "postgres"],
      ["bus", "events", "queue", "kafka"],
      ["stripe", "Stripe", "external", "external"],
    ],
    edges: [
      ["in", "gw"],
      ["gw", "users"],
      ["gw", "orders"],
      ["gw", "search"],
      ["orders", "billing"],
      ["orders", "bus"],
      ["bus", "search"],
      ["users", "db"],
      ["orders", "db"],
      ["billing", "stripe"],
    ],
  },
  "service-agent": {
    nodes: [
      ["in", "Internet", "ingress", "ingress"],
      ["web", "support-portal", "service", "deployment"],
      ["db", "postgres", "data", "postgres"],
      ["agent", "triage-agent", "agent", "agent"],
      ["llm", "Model gateway", "external", "external"],
    ],
    edges: [
      ["in", "web"],
      ["web", "db"],
      ["web", "agent"],
      ["agent", "db"],
      ["agent", "llm"],
    ],
  },
  agent: {
    nodes: [
      ["sched", "every 15 min", "schedule", "schedule"],
      ["agent", "bdr-outreach", "agent", "agent"],
      ["llm", "Model gateway", "external", "external"],
      ["crm", "HubSpot", "external", "external"],
    ],
    edges: [
      ["sched", "agent"],
      ["agent", "llm"],
      ["agent", "crm"],
    ],
  },
  functions: {
    nodes: [
      ["http", "HTTP", "trigger", "http"],
      ["bucket", "uploads", "trigger", "s3"],
      ["resize", "resize", "function", "function"],
      ["thumb", "thumbnail", "function", "function"],
      ["notify", "notify", "function", "function"],
      ["out", "media", "data", "s3"],
    ],
    edges: [
      ["http", "notify"],
      ["bucket", "resize"],
      ["resize", "thumb"],
      ["thumb", "out"],
      ["resize", "out"],
    ],
  },
  scheduled: {
    nodes: [
      ["sched", "0 2 * * *", "schedule", "schedule"],
      ["job", "nightly-report", "worker", "cronjob"],
      ["db", "warehouse", "data", "postgres"],
    ],
    edges: [
      ["sched", "job"],
      ["job", "db"],
    ],
  },
  task: {
    nodes: [
      ["job", "migrate", "worker", "job"],
      ["db", "postgres", "data", "postgres"],
    ],
    edges: [["job", "db"]],
  },
  workflow: {
    nodes: [
      ["trigger", "on push", "trigger", "git"],
      ["build", "build", "worker", "workflow"],
      ["deploy", "deploy", "worker", "workflow"],
    ],
    edges: [
      ["trigger", "build"],
      ["build", "deploy"],
    ],
  },
  mixed: {
    nodes: [
      ["in", "Internet", "ingress", "ingress"],
      ["web", "web", "service", "deployment"],
      ["hook", "webhook", "function", "function"],
      ["nightly", "nightly", "worker", "cronjob"],
      ["db", "postgres", "data", "postgres"],
    ],
    edges: [
      ["in", "web"],
      ["in", "hook"],
      ["web", "db"],
      ["hook", "db"],
      ["nightly", "db"],
    ],
  },
};

export function makeApp(
  topology: TopologyKind,
  {
    seed = 3,
    now = Date.UTC(2026, 8, 28, 14, 0, 0),
    incident = false,
    name,
  }: { seed?: number; now?: number; incident?: boolean; name?: string } = {}
): AppSnapshot {
  const rng = mulberry32(seed);
  const shape = SHAPES[topology];
  const nodes: AppNode[] = shape.nodes.map(([id, n, role, kind], i) => {
    const failing = incident && i === Math.min(2, shape.nodes.length - 1);
    const load = rng() * 0.8;
    return {
      id,
      name: n,
      role,
      kind,
      health: failing ? "failing" : load < 0.05 ? "idle" : "ok",
      load: failing ? 0.97 : load,
      replicas:
        role === "service" || role === "worker"
          ? { ready: failing ? 1 : 3, desired: 3 }
          : undefined,
      rps: role === "data" || role === "external" ? undefined : Math.round(rng() * 400),
      p50: role === "service" ? Math.round(20 + rng() * 80) : undefined,
    };
  });
  const failingId = nodes.find((n) => n.health === "failing")?.id;
  const edges: AppEdge[] = shape.edges.map(([from, to]) => ({
    from,
    to,
    rps: Math.round(10 + rng() * 300),
    errorRate: to === failingId ? 0.22 : rng() * 0.01,
  }));
  return {
    now,
    app: { slug: `demo-${topology}`, name: name ?? `demo-${topology}` },
    topology,
    nodes,
    edges,
    events: [],
  };
}

/** Advance live traffic: rates and loads drift, functions get invoked. */
export function stepApp(prev: AppSnapshot, rng: () => number, dtMs: number): AppSnapshot {
  const now = prev.now + dtMs;
  const events = prev.events.filter((e) => now - e.at < 12_000);
  let seq = prev.events.length ? Number(prev.events[prev.events.length - 1].id.slice(1)) + 1 : 0;
  const nodes = prev.nodes.map((n) => {
    if (n.health === "failing") return n;
    const load = Math.max(0, Math.min(1, n.load + (rng() - 0.5) * 0.2));
    if ((n.role === "function" || n.role === "agent") && rng() < 0.4) {
      events.push({
        id: `e${seq++}`,
        kind: n.role === "agent" ? "agent_action" : "invoked",
        nodeId: n.id,
        at: now,
      });
    }
    return { ...n, load, health: load < 0.05 ? ("idle" as const) : ("ok" as const) };
  });
  const edges = prev.edges.map((e) => ({
    ...e,
    rps: Math.max(0, Math.round(e.rps * (0.85 + rng() * 0.3))),
  }));
  return { ...prev, now, nodes, edges, events };
}
