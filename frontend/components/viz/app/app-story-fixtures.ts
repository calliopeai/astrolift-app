import type { AppNode, AppNodeRole, AppSnapshot } from "../core/app-model";
import { makeApp } from "../core/app-model";

/**
 * Snapshots the app renderer stories share (AppIsometric, AppGraph): a quiet
 * app, an incident, the largest shape we draw (20 nodes) and long names.
 */

/** Recent invokes and agent actions, at several ages, so fades show their range. */
export function withEvents(s: AppSnapshot): AppSnapshot {
  const events = s.nodes
    .filter((n) => n.role === "function" || n.role === "agent")
    .flatMap((n, i) =>
      [300 + i * 700, 4000 + i * 500, 9000].map((age, k) => ({
        id: `s${i}-${k}`,
        kind: n.role === "agent" ? ("agent_action" as const) : ("invoked" as const),
        nodeId: n.id,
        at: s.now - age,
      }))
    );
  return { ...s, events };
}

export function quietApp(): AppSnapshot {
  const s = makeApp("microservices", { name: "shop" });
  return {
    ...s,
    nodes: s.nodes.map((n) => ({ ...n, health: "idle" as const, load: 0.02, rps: 0 })),
    edges: s.edges.map((e) => ({ ...e, rps: 0, errorRate: 0 })),
  };
}

export function incidentApp(): AppSnapshot {
  const s = makeApp("microservices", { incident: true, seed: 5, name: "shop" });
  return {
    ...s,
    nodes: s.nodes.map((n) =>
      n.id === "search"
        ? { ...n, load: 0.92, replicas: { ready: 2, desired: 4 } }
        : n.id === "bus"
          ? { ...n, load: 0.85, health: "degraded" as const }
          : n
    ),
    events: [{ id: "x0", kind: "error_spike", nodeId: "orders", at: s.now - 2000 }],
  };
}

/** A mixed app with a function and an agent, failing, with recent events. */
export function incidentMixed(): AppSnapshot {
  const s = makeApp("mixed", { incident: true, seed: 9, name: "support" });
  const agent: AppNode = {
    id: "agent",
    name: "triage-agent",
    role: "agent",
    kind: "agent",
    health: "ok",
    load: 0.6,
    rps: 4,
  };
  return withEvents({
    ...s,
    nodes: [...s.nodes, agent],
    edges: [...s.edges, { from: "agent", to: "db", rps: 12, errorRate: 0 }],
  });
}

const SPEC: [string, AppNodeRole][] = [
  ["Internet", "ingress"],
  ["partner-api", "ingress"],
  ["uploads", "trigger"],
  ["gateway", "service"],
  ["users", "service"],
  ["orders", "service"],
  ["billing", "service"],
  ["search", "service"],
  ["catalog", "service"],
  ["inventory", "service"],
  ["mailer", "worker"],
  ["resize", "function"],
  ["thumbnail", "function"],
  ["notify", "function"],
  ["triage-agent", "agent"],
  ["postgres", "data"],
  ["redis", "data"],
  ["media", "data"],
  ["events", "queue"],
  ["Stripe", "external"],
];

const EDGES: [number, number][] = [
  [0, 3],
  [1, 3],
  [2, 11],
  [3, 4],
  [3, 5],
  [3, 7],
  [3, 8],
  [5, 6],
  [5, 9],
  [5, 18],
  [18, 10],
  [18, 7],
  [11, 12],
  [12, 17],
  [13, 16],
  [4, 15],
  [5, 15],
  [8, 16],
  [9, 15],
  [6, 19],
  [14, 15],
  [10, 13],
];

/** The most we draw: 20 nodes of every role. */
export function twentyNodes(): AppSnapshot {
  const base = makeApp("microservices", { name: "marketplace" });
  const nodes: AppNode[] = SPEC.map(([name, role], i) => ({
    id: `n${i}`,
    name,
    role,
    kind: role,
    health: i === 9 ? "failing" : "ok",
    load: i === 9 ? 0.97 : 0.15 + ((i * 37) % 70) / 100,
    replicas:
      role === "service" || role === "worker" ? { ready: i === 9 ? 1 : 3, desired: 3 } : undefined,
    rps: role === "data" || role === "external" ? undefined : 20 + ((i * 53) % 300),
  }));
  return withEvents({
    ...base,
    topology: "mixed",
    nodes,
    edges: EDGES.map(([a, b], i) => ({
      from: `n${a}`,
      to: `n${b}`,
      rps: 10 + ((i * 71) % 400),
      errorRate: b === 9 ? 0.18 : 0.002,
    })),
  });
}

export function longStrings(): AppSnapshot {
  const s = makeApp("service-agent", { name: "customer-success-analytics-warehouse-portal" });
  return withEvents({
    ...s,
    nodes: s.nodes.map((n) => ({
      ...n,
      name: `${n.name}-with-a-very-long-workload-identifier-that-overflows`,
    })),
  });
}
