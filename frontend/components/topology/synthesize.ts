/**
 * Topology synthesis — turn the GraphQL app + workloads pair into the
 * (nodes, edges) shape the AppTopologyMap component consumes.
 *
 * Extracted from `apps/[slug]/app-detail-client.tsx` (#705) so the
 * dedicated Topology tab can render the same graph as the Overview
 * thumbnail without duplicating the synthesis rules.
 */

import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
} from "@/graphql/registry/registry.types";

import type { TopologyEdge, TopologyNode, TopologyNodeStatus } from "./types";

export function appTopology(
  app: AstroliftRegisteredApp,
  workloads: AstroliftWorkload[]
): { nodes: TopologyNode[]; edges: TopologyEdge[] } {
  const nodes: TopologyNode[] = [];
  const edges: TopologyEdge[] = [];

  const appStatus: TopologyNodeStatus =
    app.provisioningStatus === "ready"
      ? "running"
      : app.provisioningStatus === "failed"
        ? "failed"
        : "provisioning";

  const publicWorkloads = workloads.filter((w) => w.isPublic);

  if (publicWorkloads.length > 0) {
    nodes.push({
      id: "ingress",
      type: "ingress",
      label: "Public ingress",
      sublabel: app.subdomain,
      status: appStatus,
      hostnames: publicWorkloads.map((w) => `${w.slug}.${app.subdomain}`),
      // #706 — drill into the domains tab (TLS, custom-domain mapping,
      // alternate hostnames). The ingress IS the rendered ALB / nginx
      // routing layer; the domains tab is where the operator manages it.
      href: `/apps/${app.slug}/domains`,
    });
  }

  for (const w of workloads) {
    const wlStatus: TopologyNodeStatus = w.replicas > 0 ? "running" : "provisioning";

    if (w.isPublic) {
      const svcId = `svc-${w.slug}`;
      nodes.push({
        id: svcId,
        type: "service",
        label: w.slug,
        sublabel: "ClusterIP",
        status: wlStatus,
        // #706 — Service is the routing layer between ingress and the
        // workload's pods. Drill straight to the workload detail —
        // operators investigating a "why isn't this responding" almost
        // always need the pod list + restart count, not the bare svc.
        href: `/apps/${app.slug}/workloads/${w.slug}`,
      });
      edges.push({ id: `e-ingress-${svcId}`, source: "ingress", target: svcId });
      edges.push({
        id: `e-${svcId}-wl-${w.slug}`,
        source: svcId,
        target: `wl-${w.slug}`,
      });
    }

    nodes.push({
      id: `wl-${w.slug}`,
      type: "workload",
      label: w.name,
      sublabel: w.kind,
      status: wlStatus,
      replicas: { ready: w.replicas, desired: w.replicas },
      hostnames: w.isPublic ? [`${w.slug}.${app.subdomain}`] : undefined,
      href: `/apps/${app.slug}/workloads/${w.slug}`,
    });
  }

  return { nodes, edges };
}
