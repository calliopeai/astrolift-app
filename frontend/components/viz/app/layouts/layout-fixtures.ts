import type { TopologyKind } from "@/lib/topology";

import { makeApp, type AppEdge, type AppNode, type AppSnapshot } from "../../core/app-model";

/**
 * Story states for the auto layouts, all built from makeApp so the numbers
 * come from the same model the page draws.
 */

/** Nothing moving: no traffic, every node idle. */
export function quietApp(topology: TopologyKind): AppSnapshot {
  const s = makeApp(topology);
  return {
    ...s,
    nodes: s.nodes.map((n) => ({
      ...n,
      health: "idle" as const,
      load: 0,
      rps: n.rps === undefined ? undefined : 0,
    })),
    edges: s.edges.map((e) => ({ ...e, rps: 0, errorRate: 0 })),
  };
}

/**
 * makeApp's incident (the third node fails), plus, where there is a queue,
 * producers outrunning consumers so the queue is backing up.
 */
export function incidentApp(topology: TopologyKind): AppSnapshot {
  const s = makeApp(topology, { incident: true, seed: 5 });
  const q = s.nodes.find((n) => n.role === "queue");
  if (!q) return s;
  return {
    ...s,
    nodes: s.nodes.map((n) => (n.id === q.id ? { ...n, load: 0.92 } : n)),
    edges: s.edges.map((e) =>
      e.to === q.id ? { ...e, rps: 420 } : e.from === q.id ? { ...e, rps: 60 } : e
    ),
  };
}

/** The microservices shape grown to `extra` more services behind the gateway. */
export function largeApp(extra = 28): AppSnapshot {
  const s = makeApp("microservices", { seed: 11 });
  const nodes: AppNode[] = [...s.nodes];
  const edges: AppEdge[] = [...s.edges];
  for (let i = 0; i < extra; i++) {
    const id = `svc${i}`;
    const failing = i === 7;
    nodes.push({
      id,
      name: `svc-${i.toString().padStart(2, "0")}`,
      role: "service",
      kind: "deployment",
      health: failing ? "failing" : "ok",
      load: failing ? 0.96 : ((i * 37) % 80) / 100,
      replicas: { ready: failing ? 1 : 2, desired: 2 },
      rps: 20 + ((i * 53) % 300),
      p50: 15 + ((i * 29) % 140),
    });
    edges.push({
      from: i % 3 === 0 ? "gw" : `svc${Math.max(0, i - 1)}`,
      to: id,
      rps: 15 + ((i * 71) % 260),
      errorRate: failing ? 0.18 : i % 9 === 4 ? 0.02 : 0.002,
    });
  }
  return { ...s, app: { ...s.app, name: "demo-microservices-large" }, nodes, edges };
}

/** Very long app and node names, to prove truncation. */
export function longStringsApp(topology: TopologyKind): AppSnapshot {
  const s = makeApp(topology, { incident: true });
  return {
    ...s,
    app: { ...s.app, name: `${s.app.name}-for-the-customer-success-analytics-warehouse` },
    nodes: s.nodes.map((n) => ({
      ...n,
      name: `${n.name}-with-a-very-long-workload-identifier-that-will-not-fit`,
    })),
  };
}
