"use client";

import {
  Background,
  BackgroundVariant,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { BotIcon, WrenchIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

import "@xyflow/react/dist/style.css";

export interface AgentActivityTool {
  id: string;
  name: string;
}

interface AgentNodeData extends Record<string, unknown> {
  label: string;
  active: boolean;
  runningCount: number;
}
interface ToolNodeData extends Record<string, unknown> {
  label: string;
  active: boolean;
}

const CANVAS_W = 520;
const CANVAS_H = 320;
const CENTER = { x: CANVAS_W / 2 - 60, y: CANVAS_H / 2 - 28 };
const RADIUS_X = 190;
const RADIUS_Y = 118;

function AgentNode({ data }: NodeProps<Node<AgentNodeData>>) {
  return (
    <div className="relative">
      {data.active && (
        <span className="absolute inset-0 -z-10 animate-ping rounded-xl bg-[var(--brand-primary)]/30" />
      )}
      <div
        className={cn(
          "flex min-w-[120px] flex-col items-center gap-1 rounded-xl border px-4 py-3 shadow-sm transition-colors",
          data.active
            ? "border-[var(--brand-primary)] bg-[var(--brand-primary)]/10"
            : "bg-card border-border"
        )}
      >
        <BotIcon
          className={cn(
            "size-6",
            data.active ? "text-[var(--brand-primary)]" : "text-muted-foreground"
          )}
        />
        <span className="max-w-[140px] truncate text-sm font-semibold">{data.label}</span>
        <span className="text-muted-foreground text-[10px] uppercase tracking-wide">
          {data.active ? `${data.runningCount} running` : "idle"}
        </span>
      </div>
      {/* Source handles on every side so radial edges leave cleanly. */}
      <Handle type="source" position={Position.Right} className="!opacity-0" />
      <Handle type="target" position={Position.Left} className="!opacity-0" />
    </div>
  );
}

function ToolNode({ data }: NodeProps<Node<ToolNodeData>>) {
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs shadow-sm transition-colors",
        data.active
          ? "border-[var(--brand-primary)]/50 bg-card"
          : "bg-muted/50 border-border text-muted-foreground"
      )}
    >
      <Handle type="target" position={Position.Left} className="!opacity-0" />
      <WrenchIcon className="size-3 shrink-0" />
      <span className="max-w-[120px] truncate">{data.label}</span>
    </div>
  );
}

const nodeTypes = { agent: AgentNode, tool: ToolNode };

/**
 * Animated agent-activity graph (#1091 viz): the agent at the center with its
 * bound tools around it. Edges march (React Flow `animated`) and the agent core
 * pulses while the agent is running; everything goes calm and muted when idle.
 *
 * Honest about data: we animate on the real live-running signal
 * (`runningCount`) and label with the real tool count — we do NOT fabricate a
 * requests/sec figure, since per-tool-call telemetry isn't wired yet (#891).
 * When that lands, feed a per-tool activity flag into `tools` to light
 * individual edges.
 */
export function AgentActivityGraph({
  agentName,
  tools,
  active,
  runningCount,
  className,
}: {
  agentName: string;
  tools: AgentActivityTool[];
  active: boolean;
  runningCount: number;
  className?: string;
}) {
  const { nodes, edges } = React.useMemo(() => {
    const agentNode: Node<AgentNodeData> = {
      id: "__agent__",
      type: "agent",
      position: CENTER,
      data: { label: agentName, active, runningCount },
      draggable: false,
      selectable: false,
    };

    const n = tools.length;
    const toolNodes: Node<ToolNodeData>[] = tools.map((tool, i) => {
      // Spread tools on an ellipse; start at the top and go clockwise.
      const angle = n === 1 ? 0 : (2 * Math.PI * i) / n - Math.PI / 2;
      return {
        id: `tool:${tool.id}`,
        type: "tool",
        position: {
          x: CENTER.x + 60 + Math.cos(angle) * RADIUS_X - 60,
          y: CENTER.y + 28 + Math.sin(angle) * RADIUS_Y - 16,
        },
        data: { label: tool.name, active },
        draggable: false,
        selectable: false,
      };
    });

    const toolEdges: Edge[] = tools.map((tool) => ({
      id: `e:${tool.id}`,
      source: "__agent__",
      target: `tool:${tool.id}`,
      animated: active,
      style: {
        stroke: active ? "var(--brand-primary)" : "var(--border)",
        strokeWidth: active ? 1.75 : 1,
        opacity: active ? 0.9 : 0.5,
      },
    }));

    return { nodes: [agentNode, ...toolNodes], edges: toolEdges };
  }, [agentName, tools, active, runningCount]);

  return (
    <div
      className={cn("bg-muted/20 relative h-[320px] w-full overflow-hidden rounded-lg", className)}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} className="opacity-40" />
      </ReactFlow>
      {tools.length === 0 && (
        <div className="text-muted-foreground pointer-events-none absolute inset-x-0 bottom-3 text-center text-xs">
          No tools bound yet — the graph fills in as skills &amp; tools are attached.
        </div>
      )}
    </div>
  );
}
