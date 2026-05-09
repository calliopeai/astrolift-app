"use client";

import { PageShell } from "@/components/PageShell";
import {
  AppTopologyMap,
  type TopologyEdge,
  type TopologyNode,
} from "@/components/topology";

// Demo dataset wired to no real data — exercises every node type and
// every status colour so the component can be eyeballed without
// running a real app through the registry.
const DEMO_NODES: TopologyNode[] = [
  {
    id: "ingress-1",
    type: "ingress",
    label: "Public ingress",
    sublabel: "ALB · 443/TCP",
    status: "running",
    hostnames: ["api.acme.dev", "checkout.acme.dev"],
  },
  {
    id: "svc-api",
    type: "service",
    label: "api",
    sublabel: "ClusterIP · 8080",
    status: "running",
    href: "#",
  },
  {
    id: "svc-checkout",
    type: "service",
    label: "checkout",
    sublabel: "ClusterIP · 8080",
    status: "running",
    href: "#",
  },
  {
    id: "wl-api",
    type: "workload",
    label: "api",
    sublabel: "Deployment",
    status: "running",
    replicas: { ready: 3, desired: 3 },
    hostnames: ["api.acme.dev"],
    href: "#",
  },
  {
    id: "wl-checkout",
    type: "workload",
    label: "checkout",
    sublabel: "Deployment",
    status: "provisioning",
    replicas: { ready: 1, desired: 2 },
    hostnames: ["checkout.acme.dev"],
    href: "#",
  },
  {
    id: "wl-worker",
    type: "workload",
    label: "background-worker",
    sublabel: "Deployment",
    status: "failed",
    replicas: { ready: 0, desired: 1 },
    href: "#",
  },
  {
    id: "ms-postgres",
    type: "managed-service",
    label: "postgres",
    sublabel: "RDS · db.t4g.medium",
    status: "running",
    href: "#",
  },
  {
    id: "ms-redis",
    type: "cache",
    label: "redis",
    sublabel: "ElastiCache · cache.t4g.small",
    status: "running",
    href: "#",
  },
  {
    id: "ext-stripe",
    type: "external",
    label: "Stripe",
    sublabel: "api.stripe.com",
    status: "unknown",
  },
];

const DEMO_EDGES: TopologyEdge[] = [
  { id: "e1", source: "ingress-1", target: "svc-api", animated: true },
  { id: "e2", source: "ingress-1", target: "svc-checkout", animated: true },
  { id: "e3", source: "svc-api", target: "wl-api" },
  { id: "e4", source: "svc-checkout", target: "wl-checkout" },
  { id: "e5", source: "wl-api", target: "ms-postgres", label: "DATABASE_URL" },
  { id: "e6", source: "wl-checkout", target: "ms-postgres", label: "DATABASE_URL" },
  { id: "e7", source: "wl-api", target: "ms-redis", label: "REDIS_URL" },
  { id: "e8", source: "wl-worker", target: "ms-postgres" },
  { id: "e9", source: "wl-checkout", target: "ext-stripe", label: "HTTPS" },
];

export default function TopologyPlaygroundPage() {
  return (
    <PageShell
      title="Topology map"
      description="Interactive demo of the app-overview topology component (spec 09 §4.2). Real data wires in via #237."
    >
      <AppTopologyMap nodes={DEMO_NODES} edges={DEMO_EDGES} height={560} />
    </PageShell>
  );
}
