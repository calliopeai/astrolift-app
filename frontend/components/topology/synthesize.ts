/**
 * Topology synthesis — turn the GraphQL app + workloads + managed
 * services into the (nodes, edges) shape the AppTopologyMap component
 * consumes.
 *
 * Extracted from `apps/[slug]/app-detail-client.tsx` (#705) so the
 * dedicated Topology tab can render the same graph as the Overview
 * thumbnail without duplicating the synthesis rules. Managed-service
 * nodes (#727) drop in when the optional `managedServices` argument
 * is supplied — backwards-compatible with callers that don't have
 * the data.
 */

import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
} from "@/graphql/registry/registry.types";

import type { TopologyEdge, TopologyNode, TopologyNodeStatus } from "./types";

/** Minimal shape needed from a managed-service row to render a topology
 *  node. Matches the existing managed-services list query payload. */
export interface TopologyManagedService {
  id: string;
  name: string;
  kind: string;
  variant?: string | null;
  status: string;
  environmentName: string;
}

export function appTopology(
  app: AstroliftRegisteredApp,
  workloads: AstroliftWorkload[],
  managedServices: TopologyManagedService[] = [],
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

  // #727 — Managed-service nodes hang off the workload layer. Each
  // active binding (Postgres / Redis / S3 / SES / etc) becomes a node
  // with an edge from every workload to it (workloads consume the
  // binding via env vars). Status maps to a small TopologyNodeStatus
  // enum so the node card colours match its provisioning state.
  const liveManagedServices = managedServices.filter((m) => m.status !== "deleted");
  for (const m of liveManagedServices) {
    const msStatus: TopologyNodeStatus =
      m.status === "active"
        ? "running"
        : m.status === "failed"
          ? "failed"
          : m.status === "provisioning" || m.status === "pending"
            ? "provisioning"
            : "unknown";
    const msId = `ms-${m.id}`;
    nodes.push({
      id: msId,
      type: "managed-service",
      label: m.name || m.kind,
      sublabel: m.variant ? `${m.kind} · ${m.variant}` : m.kind,
      status: msStatus,
      // Click drills into the managed-services tab; the
      // ServiceDetailSheet from #709 opens via row click there.
      // Eventually a deep-link param can auto-open the sheet for
      // this service id; out of scope today.
      href: `/apps/${app.slug}/managed-services`,
    });
    // Every workload consumes every binding by default — Astrolift
    // injects connection envs into every pod. If we grow per-workload
    // binding scoping later, this fan-out gets a filter.
    for (const w of workloads) {
      edges.push({
        id: `e-wl-${w.slug}-${msId}`,
        source: `wl-${w.slug}`,
        target: msId,
      });
    }
  }

  return { nodes, edges };
}
