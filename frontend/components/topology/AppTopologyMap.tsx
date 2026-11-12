"use client";

import {
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import { BoxIcon, CloudIcon, DatabaseIcon, GlobeIcon, NetworkIcon, ZapIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { FlowGraph } from "@/components/viz/flow-graph";
import { cn } from "@/lib/utils";

import { autoLayout } from "./layout";
import type { TopologyEdge, TopologyNode, TopologyNodeStatus, TopologyNodeType } from "./types";

// ─── status styling ───────────────────────────────────────────────────────────

const STATUS_RING: Record<TopologyNodeStatus, string> = {
  running: "ring-success bg-success/5",
  provisioning: "ring-warning bg-warning/5",
  failed: "ring-destructive/60 bg-destructive/5",
  unknown: "ring-muted-foreground/20 bg-muted/40",
};

const STATUS_DOT: Record<TopologyNodeStatus, string> = {
  running: "bg-success",
  provisioning: "bg-warning animate-pulse",
  failed: "bg-destructive",
  unknown: "bg-muted-foreground/40",
};

// ─── node renderer ────────────────────────────────────────────────────────────

const TYPE_ICON: Record<TopologyNodeType, React.ComponentType<{ className?: string }>> = {
  ingress: GlobeIcon,
  service: NetworkIcon,
  workload: BoxIcon,
  "managed-service": DatabaseIcon,
  cache: ZapIcon,
  external: CloudIcon,
};

function TopologyNodeCard({ data }: NodeProps<Node<TopologyNode>>) {
  const Icon = TYPE_ICON[data.type] ?? BoxIcon;
  const ratio = data.replicas ? `${data.replicas.ready}/${data.replicas.desired}` : null;

  return (
    <div
      className={cn(
        "viz-node bg-background max-w-[220px] min-w-[180px] rounded-md border p-3 shadow-sm ring-1",
        STATUS_RING[data.status]
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground/30" />
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground/30" />

      <div className="flex items-start gap-2">
        <div className="bg-muted mt-0.5 rounded-sm p-1">
          <Icon className="text-muted-foreground size-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className={cn("viz-node-dot size-1.5 rounded-full", STATUS_DOT[data.status])} />
            <div className="truncate text-sm leading-tight font-medium">{data.label}</div>
          </div>
          {data.sublabel && (
            <div className="text-2xs text-muted-foreground mt-0.5 truncate font-mono">
              {data.sublabel}
            </div>
          )}
        </div>
        {ratio && (
          <Badge variant="outline" className="text-2xs font-mono">
            {ratio}
          </Badge>
        )}
      </div>

      {data.hostnames && data.hostnames.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {data.hostnames.slice(0, 3).map((host) => (
            <Badge key={host} variant="secondary" className="text-2xs font-mono font-normal">
              {host}
            </Badge>
          ))}
          {data.hostnames.length > 3 && (
            <Badge variant="outline" className="text-2xs font-mono">
              +{data.hostnames.length - 3}
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}

const NODE_TYPES: NodeTypes = {
  topology: TopologyNodeCard,
};

// ─── public component ─────────────────────────────────────────────────────────

export interface AppTopologyMapProps {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  /** Override the default click-to-navigate handler. */
  onNodeClick?: (node: TopologyNode) => void;
  /** Map height — defaults to 480px so it fits inside an app-overview card. */
  height?: number | string;
  className?: string;
  /** Visual treatment — forwarded to `FlowGraph` (#1055). */
  variant?: "default" | "telemetry";
}

/**
 * Interactive topology of an app's provisioned components. Spec 09 §4.2.
 *
 * Pass a flat list of nodes (any subset of ingress / service / workload
 * / managed-service / cache / external) and the edges between them.
 * Auto-layout positions them left-to-right by tier; node cards show
 * status, replica counts, and hostnames where applicable.
 *
 * Built on @xyflow/react, so zoom / pan / minimap come for free.
 */
export function AppTopologyMap({
  nodes,
  edges,
  onNodeClick,
  height = 480,
  className,
  variant,
}: AppTopologyMapProps) {
  const router = useRouter();

  const flowNodes = React.useMemo<Node<TopologyNode>[]>(
    () =>
      autoLayout(nodes).map((n) => ({
        id: n.id,
        type: "topology",
        position: n.position,
        data: n,
      })),
    // Auto-layout runs once on mount. Callers that want to re-flow
    // when their data changes should remount via a `key` prop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const flowEdges = React.useMemo<Edge[]>(
    () =>
      edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label,
        animated: e.animated ?? false,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const handleNodeClick = React.useCallback(
    (_id: string, data: unknown) => {
      const node = data as TopologyNode;
      if (onNodeClick) {
        onNodeClick(node);
        return;
      }
      if (node.href) router.push(node.href);
    },
    [onNodeClick, router]
  );

  return (
    <FlowGraph
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={NODE_TYPES}
      onNodeClick={handleNodeClick}
      height={height}
      className={className}
      variant={variant}
    />
  );
}
