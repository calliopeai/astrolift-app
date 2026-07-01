"use client";

import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  BoxIcon,
  CloudIcon,
  DatabaseIcon,
  GlobeIcon,
  NetworkIcon,
  ZapIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { autoLayout } from "./layout";
import type {
  TopologyEdge,
  TopologyNode,
  TopologyNodeStatus,
  TopologyNodeType,
} from "./types";

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
  const ratio = data.replicas
    ? `${data.replicas.ready}/${data.replicas.desired}`
    : null;

  return (
    <div
      className={cn(
        "min-w-[180px] max-w-[220px] rounded-md border bg-background p-3 ring-1 shadow-sm",
        STATUS_RING[data.status],
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground/30" />
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground/30" />

      <div className="flex items-start gap-2">
        <div className="mt-0.5 rounded-sm bg-muted p-1">
          <Icon className="size-3.5 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className={cn("size-1.5 rounded-full", STATUS_DOT[data.status])} />
            <div className="truncate text-sm font-medium leading-tight">{data.label}</div>
          </div>
          {data.sublabel && (
            <div className="mt-0.5 truncate font-mono text-2xs text-muted-foreground">
              {data.sublabel}
            </div>
          )}
        </div>
        {ratio && (
          <Badge variant="outline" className="font-mono text-2xs">
            {ratio}
          </Badge>
        )}
      </div>

      {data.hostnames && data.hostnames.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {data.hostnames.slice(0, 3).map((host) => (
            <Badge
              key={host}
              variant="secondary"
              className="font-mono text-2xs font-normal"
            >
              {host}
            </Badge>
          ))}
          {data.hostnames.length > 3 && (
            <Badge variant="outline" className="font-mono text-2xs">
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
}: AppTopologyMapProps) {
  const router = useRouter();

  const initialNodes = React.useMemo<Node<TopologyNode>[]>(
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
    [],
  );

  const initialEdges = React.useMemo<Edge[]>(
    () =>
      edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label,
        animated: e.animated ?? false,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { strokeWidth: 1.5 },
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [flowNodes, , onNodesChange] = useNodesState(initialNodes);
  const [flowEdges, , onEdgesChange] = useEdgesState(initialEdges);

  // ReactFlow needs a DOM-measurement pass before fitView can position
  // nodes correctly. Show a skeleton until onInit fires so the graph
  // never flashes blank or with nodes stacked at origin.
  const [isReady, setIsReady] = React.useState(false);
  const handleInit = React.useCallback(() => {
    setIsReady(true);
  }, []);

  const handleNodeClick = React.useCallback(
    (_: React.MouseEvent, node: Node<TopologyNode>) => {
      if (onNodeClick) {
        onNodeClick(node.data);
        return;
      }
      if (node.data.href) {
        router.push(node.data.href);
      }
    },
    [onNodeClick, router],
  );

  return (
    <div
      className={cn("relative rounded-md border bg-card", className)}
      style={{ height: typeof height === "number" ? `${height}px` : height }}
    >
      {!isReady && (
        <div className="absolute inset-0 z-10 p-4">
          <Skeleton className="h-full w-full" />
        </div>
      )}
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onInit={handleInit}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1.2 }}
        minZoom={0.4}
        maxZoom={1.8}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} />
        <Controls position="bottom-right" showInteractive={false} />
        <MiniMap
          position="bottom-left"
          pannable
          zoomable
          nodeStrokeWidth={2}
          className="!bg-card !rounded-md !border"
        />
      </ReactFlow>
    </div>
  );
}
