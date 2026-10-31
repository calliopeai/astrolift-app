import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { FlowGraph } from "@/components/viz/flow-graph";
import { ProjectTopologyNode } from "./ProjectTopologyNode";
const meta: Meta = { title: "Screens/Projects/TopologyNode", parameters: { layout: "padded" } };
export default meta;
export const Running: StoryObj = {
  render: () => (
    <FlowGraph
      nodes={[
        {
          id: "engineer",
          type: "component",
          position: { x: 0, y: 0 },
          data: {
            label: "Implementation engineer",
            kind: "agent",
            status: "running",
            description: "",
            fields: [],
          },
        },
      ]}
      edges={[]}
      nodeTypes={{ component: ProjectTopologyNode }}
      height={240}
    />
  ),
};
