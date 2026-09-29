import { describe, expect, it } from "vitest";

import { appTopology } from "@/components/topology";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";

import { topologySnapshot } from "./topology-snapshot";

const app = {
  slug: "checkout",
  name: "checkout",
  subdomain: "checkout",
  provisioningStatus: "ready",
} as AstroliftRegisteredApp;

const wl = (slug: string, kind: string, isPublic = false, replicas = 1) =>
  ({ id: slug, slug, name: slug, kind, isPublic, replicas }) as AstroliftWorkload;

describe("topologySnapshot", () => {
  const { nodes, edges } = appTopology(
    app,
    [wl("web", "deployment", true, 2), wl("worker", "deployment")],
    [{ id: "db", name: "orders-db", kind: "postgres", status: "active", environmentName: "prod" }]
  );
  const snap = topologySnapshot(app, nodes, edges, 0);

  it("folds the k8s Service into one ingress-to-workload hop", () => {
    expect(snap.nodes.map((n) => n.id)).not.toContain("svc-web");
    expect(snap.edges).toContainEqual({ from: "ingress", to: "wl-web", rps: 0, errorRate: 0 });
  });

  it("gives each node a role from its kind and name", () => {
    const role = Object.fromEntries(snap.nodes.map((n) => [n.id, n.role]));
    expect(role).toMatchObject({
      ingress: "ingress",
      "wl-web": "service",
      "wl-worker": "worker",
      "ms-db": "data",
    });
    expect(snap.topology).toBe("service-worker");
  });

  it("invents no traffic: every rate and load is zero", () => {
    expect(snap.edges.every((e) => e.rps === 0 && e.errorRate === 0)).toBe(true);
    expect(snap.nodes.every((n) => n.load === 0 && n.rps === undefined)).toBe(true);
  });

  it("maps provisioning state onto health", () => {
    const failed = topologySnapshot(
      app,
      nodes.map((n) => (n.id === "wl-worker" ? { ...n, status: "failed" as const } : n)),
      edges,
      0
    );
    expect(failed.nodes.find((n) => n.id === "wl-worker")?.health).toBe("failing");
    expect(failed.nodes.find((n) => n.id === "wl-web")?.health).toBe("ok");
  });
});
