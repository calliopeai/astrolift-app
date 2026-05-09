/**
 * Topology map types — see spec 09 §4.2.
 *
 * Designed for the app-overview surface: workloads, the Services that
 * route to them, the Ingress rules that expose those Services, and the
 * managed services (databases, caches, queues) the workloads consume.
 */

export type TopologyNodeType =
  | "ingress"
  | "service"
  | "workload"
  | "managed-service"
  | "cache"
  | "external";

export type TopologyNodeStatus =
  | "running"
  | "provisioning"
  | "failed"
  | "unknown";

// Index signature is required by @xyflow/react's `Node<TData>`
// constraint — every node-data type must be assignable to
// `Record<string, unknown>`. The signature is structural noise; the
// named fields below are the real contract.
export interface TopologyNode extends Record<string, unknown> {
  id: string;
  type: TopologyNodeType;
  label: string;
  /** Sub-label rendered under the main label — slug, kind, port, etc. */
  sublabel?: string;
  status: TopologyNodeStatus;
  /** Click target (used by the default onNodeClick handler). */
  href?: string;
  /** For workloads: replica count + health summary. */
  replicas?: { ready: number; desired: number };
  /** For ingress / public workloads: visible hostnames. */
  hostnames?: string[];
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  /** Optional inline label (e.g. "DATABASE_URL", "8080→3306"). */
  label?: string;
  /** Animate the dash flow — useful for live request paths. */
  animated?: boolean;
}
