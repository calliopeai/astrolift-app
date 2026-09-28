import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Handle, type NodeProps, Position } from "@xyflow/react";

import { FlowGraph } from "@/components/viz/flow-graph";

/** The shared visualizer (LiveFlowMap): nodes, edges, pan and zoom. */
const meta: Meta = { title: "Patterns/Viz/FlowGraph", parameters: { layout: "padded" } };
export default meta;

function Box({ data }: NodeProps) {
  return (
    <div className="bg-card border-border rounded-md border px-3 py-2 font-mono text-xs">
      <Handle type="target" position={Position.Left} />
      {String((data as { label: string }).label)}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export const Default: StoryObj = {
  render: () => (
    <FlowGraph
      height={320}
      nodeTypes={{ box: Box }}
      nodes={[
        { id: "a", type: "box", position: { x: 0, y: 80 }, data: { label: "webhook" } },
        { id: "b", type: "box", position: { x: 200, y: 20 }, data: { label: "support-bot" } },
        { id: "c", type: "box", position: { x: 200, y: 140 }, data: { label: "triage-agent" } },
        { id: "d", type: "box", position: { x: 400, y: 80 }, data: { label: "reply" } },
      ]}
      edges={[
        { id: "ab", source: "a", target: "b" },
        { id: "ac", source: "a", target: "c" },
        { id: "bd", source: "b", target: "d" },
        { id: "cd", source: "c", target: "d" },
      ]}
    />
  ),
};
