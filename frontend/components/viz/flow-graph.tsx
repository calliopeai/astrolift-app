"use client";

import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export interface FlowGraphProps {
  /** Already-positioned React-Flow nodes (with `type` matching `nodeTypes`). */
  nodes: Node[];
  edges: Edge[];
  /** Custom node renderers, keyed by `node.type`. */
  nodeTypes: NodeTypes;
  /** Container height. Default 480px so it fits inside an overview card. */
  height?: number | string;
  className?: string;
  /** Click handler; receives the node id + its `data`. Falls back to no-op. */
  onNodeClick?: (id: string, data: unknown) => void;
  fitViewOptions?: { padding?: number; maxZoom?: number };
  minZoom?: number;
  maxZoom?: number;
  /** Show the minimap. Off is better for small DAGs. Default true. */
  showMiniMap?: boolean;
}

/**
 * Generic, data-agnostic `@xyflow/react` shell.
 *
 * Extracted from `AppTopologyMap` so every graph surface — topology, the
 * pipeline/deployment DAG, the workflow builder — shares one renderer: zoom /
 * pan / minimap, a skeleton until the first measurement pass, sensible edge
 * defaults, and hidden attribution. Callers own their layout (they pass
 * positioned nodes) and their node renderers (`nodeTypes`).
 *
 * Nodes/edges are read once on mount (React-Flow owns interaction state after
 * that). Remount via a `key` prop to re-flow when the source data changes.
 *
 * Motion: edge `animated` dashes and any node pulse are neutralised globally
 * by the `prefers-reduced-motion` reset in globals.css, so no per-graph gate.
 */
export function FlowGraph({
  nodes,
  edges,
  nodeTypes,
  height = 480,
  className,
  onNodeClick,
  fitViewOptions = { padding: 0.2, maxZoom: 1.2 },
  minZoom = 0.4,
  maxZoom = 1.8,
  showMiniMap = true,
}: FlowGraphProps) {
  // Fill in edge defaults (arrowhead + stroke) without clobbering caller
  // overrides. Computed once — the graph reads its initial data on mount.
  const initialEdges = React.useMemo<Edge[]>(
    () =>
      edges.map((e) => ({
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { strokeWidth: 1.5 },
        ...e,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [flowNodes, , onNodesChange] = useNodesState(nodes);
  const [flowEdges, , onEdgesChange] = useEdgesState(initialEdges);

  // ReactFlow needs a DOM-measurement pass before fitView can place nodes;
  // show a skeleton until onInit fires so the graph never flashes blank.
  const [isReady, setIsReady] = React.useState(false);
  const handleInit = React.useCallback(() => setIsReady(true), []);

  const handleNodeClick = React.useCallback(
    (_: React.MouseEvent, node: Node) => onNodeClick?.(node.id, node.data),
    [onNodeClick],
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
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onInit={handleInit}
        fitView
        fitViewOptions={fitViewOptions}
        minZoom={minZoom}
        maxZoom={maxZoom}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} />
        <Controls position="bottom-right" showInteractive={false} />
        {showMiniMap && (
          <MiniMap
            position="bottom-left"
            pannable
            zoomable
            nodeStrokeWidth={2}
            className="!bg-card !rounded-md !border"
          />
        )}
      </ReactFlow>
    </div>
  );
}
